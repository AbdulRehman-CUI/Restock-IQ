from sqlalchemy import insert, select
from app.core.errors import NotFound
from app.db.models import Dataset, Job, ModelRun, Product, DailySale, TABLES


def add_dataset(session, dataset_id, batch, manifest, quality):
    session.add(Dataset(id=dataset_id, provenance=manifest["provenance"],
                        start_date=batch.start_date, end_date=batch.end_date,
                        manifest=manifest, quality=quality))
    session.flush()
    for filename, model in TABLES.items():
        rows = []
        for record in batch.tables[filename]:
            row = record.model_dump(mode="python")
            if filename == "supplier_quotes.csv":
                row["observed_at"] = row["observed_at"].isoformat()
            rows.append({"dataset_id": dataset_id, **row})
        if rows:
            session.execute(insert(model), rows)


def get_summary(db, dataset_id):
    with db.sessions() as session:
        dataset = session.get(Dataset, str(dataset_id))
        if dataset is None:
            raise NotFound("Dataset not found")
        return {"dataset_id": dataset.id, "manifest": dataset.manifest, **dataset.quality}


def get_job(db, job_id):
    with db.sessions() as session:
        job = session.get(Job, str(job_id))
        if job is None:
            raise NotFound("Job not found")
        result = {key: getattr(job, key) for key in (
            "id", "status", "progress", "stage", "created_at", "updated_at",
            "dataset_id", "error", "error_id", "configuration")}
        result["model_id"] = session.scalar(select(ModelRun.id).where(
            ModelRun.job_id == job.id, ModelRun.role == "final_inference"))
        return result


def load_demand_data(db, dataset_id):
    """Operational allowlist only: products/category and observed sales/flags."""
    import pandas as pd
    with db.sessions() as session:
        dataset = session.get(Dataset, str(dataset_id))
        if dataset is None:
            raise NotFound("Dataset not found")
        products = session.execute(select(Product.sku, Product.category).where(Product.dataset_id == str(dataset_id))).mappings().all()
        sales = session.execute(select(DailySale.date, DailySale.sku, DailySale.units_sold,
            DailySale.promo_active, DailySale.fully_in_stock, DailySale.store_open).where(
                DailySale.dataset_id == str(dataset_id)).order_by(DailySale.sku, DailySale.date)).mappings().all()
        return pd.DataFrame(sales), pd.DataFrame(products), {
            "dataset_id": dataset.id, "provenance": dataset.provenance,
            "start_date": dataset.start_date, "end_date": dataset.end_date,
            "file_hashes": dataset.manifest["file_hashes"], "timezone": dataset.manifest["timezone"]}


def model_record(db, model_id):
    with db.sessions() as session:
        run = session.get(ModelRun, str(model_id))
        if run is None:
            raise NotFound("Model run not found")
        return {column.name: getattr(run, column.name) for column in ModelRun.__table__.columns}


def load_business_inputs(db, dataset_id):
    """Materialize validated operational contracts; never load evaluation truth."""
    from app.data.contracts import CONTRACTS
    from pydantic import ValidationError
    from app.core.errors import StateConflict
    result = {}
    with db.sessions() as session:
        try:
            for name in ("products.csv", "inventory.csv", "supplier_quotes.csv", "purchase_orders.csv", "fx_rates.csv"):
                model, contract = TABLES[name], CONTRACTS[name]
                records = session.scalars(select(model).where(model.dataset_id == str(dataset_id)))
                result[name] = [contract.model_validate({field: getattr(row, field) for field in contract.model_fields}) for row in records]
        except ValidationError as exc:
            raise StateConflict("Stored business data fails its operational contract; import a corrected dataset") from exc
    return result
