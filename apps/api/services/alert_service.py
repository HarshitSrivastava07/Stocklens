"""Alert evaluation service stub — checks all active alerts."""
import logging
from sqlalchemy import select
from dependencies.db import AsyncSessionLocal
from models.db_models import Alert, RealtimeQuote

log = logging.getLogger("alert_service")

async def run_alert_evaluation():
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(Alert).where(Alert.is_active == True, Alert.is_triggered == False))
        alerts = r.scalars().all()
        triggered = 0
        for alert in alerts:
            try:
                q_res = await db.execute(select(RealtimeQuote).where(RealtimeQuote.nse_symbol == alert.nse_symbol))
                quote = q_res.scalar_one_or_none()
                if not quote or not quote.ltp:
                    continue
                ltp = float(quote.ltp)
                cv = float(alert.condition_value) if alert.condition_value else None
                fired = False
                if alert.alert_type == "PRICE_ABOVE" and cv and ltp >= cv:
                    fired = True
                elif alert.alert_type == "PRICE_BELOW" and cv and ltp <= cv:
                    fired = True
                if fired:
                    from datetime import datetime, timezone
                    alert.is_triggered = True
                    alert.last_triggered = datetime.now(timezone.utc)
                    triggered += 1
            except Exception as e:
                log.warning(f"Alert eval error: {e}")
        await db.commit()
        log.info(f"Alerts evaluated: triggered={triggered}/{len(alerts)}")
