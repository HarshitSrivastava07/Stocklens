from fastapi import APIRouter
router = APIRouter()

@router.post("/run", status_code=202)
async def run_backtest(body: dict):
    return {"message": "Backtest queued", "id": "placeholder"}

@router.get("/{backtest_id}/results")
async def get_backtest_results(backtest_id: str):
    return {"id": backtest_id, "results": [], "message": "Backtest results after computation"}
