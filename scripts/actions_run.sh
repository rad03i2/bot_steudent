#!/usr/bin/env bash
# تشغيل البوت داخل GitHub Actions مع حفظ قاعدة البيانات مشفّرة في فرع bot-data.
#
# - يسترجع آخر نسخة من البيانات عند البدء.
# - يشغّل البوت لمدة RUN_SECONDS (أقل من حد الست ساعات لـ GitHub Actions).
# - يحفظ البيانات كل SAVE_EVERY ثانية وعند الإيقاف.
# البيانات مشفّرة بـ AES-256 بمفتاح DATA_KEY (السر)، لأن المستودع عام.
set -euo pipefail

RUN_SECONDS="${RUN_SECONDS:-20400}"   # 5 ساعات و40 دقيقة
SAVE_EVERY="${SAVE_EVERY:-600}"       # كل 10 دقائق
DATA_BRANCH="${DATA_BRANCH:-bot-data}"
DB_PATH="${DB_PATH:-data/study_bot.db}"
ENC_NAME="study_bot.db.enc"
WORK="$(mktemp -d)"

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "::notice::السر TELEGRAM_BOT_TOKEN غير موجود بعد. أضفه من Settings > Secrets and variables > Actions."
  exit 0
fi
DATA_KEY="${DATA_KEY:-$TELEGRAM_BOT_TOKEN}"
export DATA_KEY
export DB_PATH

mkdir -p "$(dirname "$DB_PATH")"

restore() {
  if git fetch --quiet --depth=1 origin "$DATA_BRANCH" 2>/dev/null; then
    git show "FETCH_HEAD:$ENC_NAME" > "$WORK/in.enc"
    openssl enc -d -aes-256-cbc -pbkdf2 -pass env:DATA_KEY -in "$WORK/in.enc" -out "$DB_PATH"
    echo "✅ تم استرجاع البيانات ($(stat -c %s "$DB_PATH") بايت)"
  else
    echo "ℹ️ لا توجد بيانات سابقة، البداية من الصفر"
  fi
}

LAST_HASH=""
save() {
  [ -f "$DB_PATH" ] || return 0
  # نسخة متسقة حتى لو البوت يكتب في نفس اللحظة
  rm -f "$WORK/snap.db"
  python - "$DB_PATH" "$WORK/snap.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()
PY
  local hash
  hash="$(sha256sum "$WORK/snap.db" | cut -d' ' -f1)"
  [ "$hash" = "$LAST_HASH" ] && return 0
  openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:DATA_KEY -in "$WORK/snap.db" -out "$WORK/out.enc"
  local blob tree commit
  blob="$(git hash-object -w "$WORK/out.enc")"
  tree="$(printf '100644 blob %s\t%s\n' "$blob" "$ENC_NAME" | git mktree)"
  # فرع بـ commit واحد فقط حتى لا يكبر حجم المستودع مع الوقت
  commit="$(git commit-tree "$tree" -m "Save bot data")"
  if git push --quiet --force origin "$commit:refs/heads/$DATA_BRANCH"; then
    LAST_HASH="$hash"
    echo "💾 تم حفظ البيانات $(date -u +%H:%M)"
  else
    echo "⚠️ فشل حفظ البيانات، سنحاول لاحقاً"
  fi
}

export GIT_AUTHOR_NAME="study-bot" GIT_AUTHOR_EMAIL="study-bot@users.noreply.github.com"
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME" GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"

restore
LAST_HASH="$( [ -f "$DB_PATH" ] && sha256sum "$DB_PATH" | cut -d' ' -f1 || true)"

timeout --signal=INT --kill-after=60 "$RUN_SECONDS" python -m study_bot &
BOT_PID=$!

# عند إلغاء التشغيل من GitHub: نوقف البوت بهدوء ونحفظ البيانات قبل الخروج
shutdown() {
  echo "⏹ طلب إيقاف، حفظ البيانات..."
  kill -INT "$BOT_PID" 2>/dev/null || true
  wait "$BOT_PID" 2>/dev/null || true
  save || true
  exit 0
}
trap shutdown INT TERM

elapsed=0
while kill -0 "$BOT_PID" 2>/dev/null; do
  sleep 15 &
  wait $! || true
  elapsed=$((elapsed + 15))
  if [ "$elapsed" -ge "$SAVE_EVERY" ]; then
    save || true
    elapsed=0
  fi
done

set +e
wait "$BOT_PID"
code=$?
set -e
save
# 124/130 = انتهى الوقت المحدد وتوقف البوت بشكل طبيعي
if [ "$code" -ne 0 ] && [ "$code" -ne 124 ] && [ "$code" -ne 130 ]; then
  echo "❌ البوت توقف بخطأ ($code)"
  exit "$code"
fi
echo "✅ انتهت الجولة، الجولة التالية تبدأ تلقائياً"
