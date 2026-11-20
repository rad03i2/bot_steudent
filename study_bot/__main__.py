"""تشغيل البوت: python -m study_bot"""

import logging
import sys

from .config import Config
from .telegram_bot import build_application


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    config = Config.from_env()
    if not config.telegram_token:
        sys.exit("❌ لازم تضبط TELEGRAM_BOT_TOKEN (شوف README.md)")
    if config.ai_enabled:
        logging.info("فهم الرسائل: Claude (%s)", config.claude_model)
    else:
        logging.info("فهم الرسائل: المحلّل المحلي المجاني (بدون أي API)")

    app = build_application(config)
    logging.info("البوت يعمل... (Ctrl+C للإيقاف)")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
