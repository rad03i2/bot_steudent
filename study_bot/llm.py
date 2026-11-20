"""فهم رسائل الطلاب باستخدام Claude وإرجاع بيانات منظمة (Structured Outputs)."""

from __future__ import annotations

import logging
from datetime import date
from typing import Literal, Optional, Protocol

import anthropic
from pydantic import BaseModel, Field, ValidationError

from .dates import calendar_reference
from .models import Task

log = logging.getLogger(__name__)

TaskKindKey = Literal[
    "final", "midterm", "exam", "quiz", "project", "presentation",
    "report", "homework", "lab", "lecture", "other",
]


class ExtractedTask(BaseModel):
    kind: TaskKindKey
    subject: str = Field(description="اسم المادة بالصيغة الموحدة")
    title: str = Field(description="وصف قصير للمحتوى أو الموضوع، أو نص فارغ")
    date_expression: str = Field(description="التعبير الزمني كما ورد حرفياً في الرسالة")
    date: Optional[str] = Field(description="التاريخ YYYY-MM-DD من التقويم المرجعي، أو null إن لم يُذكر")
    time: Optional[str] = Field(description="الوقت HH:MM بنظام 24 ساعة أو null")
    notes: Optional[str] = Field(description="ملاحظات إضافية مهمة أو null")


class TaskChanges(BaseModel):
    kind: Optional[TaskKindKey]
    subject: Optional[str]
    title: Optional[str]
    date_expression: Optional[str]
    date: Optional[str]
    time: Optional[str]
    notes: Optional[str]


class QuerySpec(BaseModel):
    start_date: str = Field(description="YYYY-MM-DD")
    end_date: str = Field(description="YYYY-MM-DD")
    subject: Optional[str]
    kinds: Optional[list[TaskKindKey]]
    title: str = Field(description="عنوان قصير للجدول مثل: جدول الأسبوع القادم")


class Analysis(BaseModel):
    intent: Literal["add", "update", "delete", "query", "ignore", "clarify"]
    tasks: list[ExtractedTask] = Field(description="المهام الجديدة عند intent=add، وإلا قائمة فارغة")
    target_task_id: Optional[int] = Field(description="رقم المهمة المقصودة عند update/delete")
    changes: Optional[TaskChanges]
    query: Optional[QuerySpec]
    reply: Optional[str] = Field(description="سؤال توضيحي عند clarify، وإلا null")


SYSTEM_PROMPT = """\
أنت "مُذكِّر الدفعة": بوت يساعد مجموعة صغيرة من طلاب الجامعة على تنظيم مواعيدهم الدراسية.
تصلك رسائل من قروب الطلاب أو من الخاص، بلهجات عربية مختلفة (عراقية، شامية، خليجية، مصرية، فصحى) وقد تختلط بالإنجليزية.
مهمتك تحليل الرسالة وإرجاع JSON حسب المخطط فقط.

أنواع النية (intent):
- add: الرسالة تُعلن عن موعد دراسي جديد أو أكثر: امتحان، كويز، ميد، فاينل، واجب، مشروع، تقرير، تسليم، عرض تقديمي، مختبر، محاضرة تعويضية...
- update: الرسالة تعدّل موعداً مسجلاً مسبقاً (تأجيل، تقديم، تغيير المحتوى أو الوقت). حدّد target_task_id من قائمة المهام المسجلة وضع التغييرات فقط في changes (والحقول غير المتغيرة null).
- delete: الرسالة تلغي موعداً مسجلاً ("انلغى الكويز"). حدّد target_task_id.
- query: الطالب يسأل عن المواعيد أو الجدول ("شنو عدنا الأسبوع الجاي؟"، "مهام الأربعاء"، "متى الفاينل؟"، "شكو باجر؟"). املأ query بنطاق التواريخ والفلاتر.
- clarify: الرسالة عن موعد دراسي لكن معلومة أساسية ناقصة أو غامضة جداً (لا يوجد يوم أبداً، أو لا يمكن معرفة المهمة المقصودة بالتعديل). ضع سؤالاً قصيراً ولطيفاً في reply باللهجة نفسها.
- ignore: أي كلام آخر (سوالف، مزاح، تحية، شكر، أسئلة عن الدروس نفسها، أخبار ماضية).

قواعد مهمة:
1. التواريخ: لا تحسب التواريخ من رأسك. استخدم "التقويم المرجعي" المرفق دائماً واختر منه التاريخ الصحيح.
   - "الخميس" أو "هذا الخميس" = أقرب خميس قادم (اليوم نفسه إن كان اليوم خميساً).
   - "الخميس الجاي/القادم" = أقرب خميس قادم بعد اليوم.
   - "الأسبوع الجاي يوم الخميس" = خميس الأسبوع القادم حسب التقويم.
   - "باجر/بكرة/غداً" = الغد.
   - انسخ التعبير الزمني حرفياً كما ورد في date_expression (مثل "الخميس الجاي" أو "باجر" أو "26/11").
2. المادة: إن كانت المادة تطابق مادة في "المواد المعروفة" (حتى لو كُتبت بشكل مختلف أو بالإنجليزية أو باختصار)، استخدم الاسم المعروف حرفياً. وإلا اكتب اسمها بالعربية بشكل نظيف بدون "ال" التعريف الزائدة إلا إن كانت جزءاً من الاسم.
3. النوع: ميد/mid/نصفي = midterm، فاينل/final/نهائي = final، كويز/quiz/اختبار قصير = quiz، امتحان/اختبار (غير محدد) = exam، واجب/homework/assignment/شيت = homework، تقرير/report = report، مشروع/project = project، برزنتيشن/عرض/سمنار = presentation، مختبر/لاب = lab، محاضرة = lecture، غير ذلك = other.
4. title: وصف قصير جداً للمحتوى ("الفصل 3 و 4"، "تمارين 4.1 إلى 4.5"). لا تكرر اسم المادة أو النوع فيه. إن لم يُذكر محتوى اتركه "".
5. رسالة واحدة قد تحتوي عدة مهام؛ أضفها كلها في tasks.
6. الاستعلام: "الأسبوع الجاي" = من أول إلى آخر يوم في الأسبوع القادم في التقويم. "هذا الأسبوع" = من اليوم إلى آخر الأسبوع الحالي. سؤال عام مثل "شنو عدنا؟" أو "شنو باقي؟" = من اليوم ولمدة 14 يوماً. "متى الفاينل/امتحان X؟" = من اليوم ولمدة 180 يوماً مع الفلاتر المناسبة. لا تضع فلتر مادة أو نوع إلا إذا ذُكر صراحة.
7. إذا كان الموعد في الماضي بوضوح (خبر عن امتحان صار) فهو ignore.
8. لا تخترع معلومات غير موجودة في الرسالة.
"""


def _tasks_context(tasks: list[Task]) -> str:
    if not tasks:
        return "(لا توجد مهام مسجلة)"
    return "\n".join(
        f"#{t.id} | {t.kind} | {t.subject} | {t.title or '-'} | {t.due_date.isoformat()}"
        + (f" {t.due_time}" if t.due_time else "")
        for t in tasks
    )


def build_user_prompt(
    message: str,
    *,
    today: date,
    week_start: int,
    sender_name: str,
    known_subjects: list[str],
    upcoming: list[Task],
    replied_to: str | None = None,
) -> str:
    parts = [
        "<calendar>\nالتقويم المرجعي (التاريخ، اليوم، الأسبوع):\n"
        + calendar_reference(today, week_start=week_start)
        + "\n</calendar>",
        "<known_subjects>\n" + ("، ".join(known_subjects) if known_subjects else "(لا يوجد بعد)") + "\n</known_subjects>",
        "<registered_tasks>\nالمهام المسجلة حالياً (رقم | نوع | مادة | محتوى | تاريخ):\n"
        + _tasks_context(upcoming)
        + "\n</registered_tasks>",
    ]
    if replied_to:
        parts.append(f"<replied_to_message>\n{replied_to}\n</replied_to_message>")
    parts.append(f"<message sender=\"{sender_name}\">\n{message}\n</message>")
    return "\n\n".join(parts)


class Analyzer(Protocol):
    async def analyze(self, user_prompt: str) -> Analysis | None: ...


class ClaudeAnalyzer:
    def __init__(self, api_key: str, model: str, effort: str = "low"):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model
        self.effort = effort

    async def analyze(self, user_prompt: str) -> Analysis | None:
        try:
            response = await self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                output_format=Analysis,
                output_config={"effort": self.effort},
                # إذا رفض النموذج الطلب لأي سبب، يعيد الخادم المحاولة بنموذج بديل تلقائياً
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError:
            log.warning("Claude rate limited")
            return None
        except anthropic.APIStatusError as e:
            log.error("Claude API error %s: %s", e.status_code, e.message)
            return None
        except anthropic.APIConnectionError:
            log.error("Claude connection error")
            return None
        except ValidationError as e:
            # يحدث عند الرفض أو انقطاع الرد قبل اكتمال JSON
            log.warning("Claude output did not match schema: %s", e)
            return None

        if response.stop_reason == "refusal":
            log.warning("Claude refused: %s", response.stop_details)
            return None
        if response.stop_reason == "max_tokens":
            log.warning("Claude hit max_tokens")
            return None
        return response.parsed_output
