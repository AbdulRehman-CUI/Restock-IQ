"""Only application-owned, registered, hash-checked joblib artifacts may load."""
from datetime import date
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import joblib
from app.core.errors import ArtifactError
from app.ml.features import FEATURE_VERSION


def dependency_versions():
    return {name: importlib.metadata.version(name) for name in
            ("scikit-learn", "joblib", "numpy", "pandas", "scipy", "threadpoolctl")}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact_directory(settings, model_id):
    identifier = str(UUID(str(model_id)))
    root = (settings.artifact_dir / "models").resolve()
    path = root / identifier
    if not path.resolve().is_relative_to(root):
        raise ArtifactError("Artifact directory escapes the configured model root")
    return path


def save_artifact(settings, model_id, candidate, metadata):
    destination = artifact_directory(settings, model_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".writing-{uuid4()}"
    temporary.mkdir(exist_ok=False)
    try:
        model_file = temporary / "pipeline.joblib"
        joblib.dump(candidate, model_file, compress=3)
        metadata_file = temporary / "metadata.json"
        metadata_file.write_text(json.dumps(metadata, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        result = {"artifact_path": str(destination / "pipeline.joblib"), "artifact_hash": digest(model_file),
                  "metadata_hash": digest(metadata_file)}
        # Publish the complete directory; no successful DB record exists until all
        # model files and the final database transaction complete.
        if settings.database_url:
            from app.db.models import ArtifactFile
            from app.db.session import Database
            db = Database(settings.runtime_dir / "restockiq.sqlite3", settings.database_url)
            try:
                with db.transaction() as session:
                    for filename in ("pipeline.joblib", "metadata.json"):
                        session.add(ArtifactFile(model_id=str(model_id), filename=filename,
                                                 payload=(temporary / filename).read_bytes()))
            finally:
                db.engine.dispose()
            result["artifact_path"] = f"database://models/{model_id}/pipeline.joblib"
        os.replace(temporary, destination)
        return result
    finally:
        # Only remove this function's known temporary files, never existing models.
        if temporary.exists():
            for name in ("pipeline.joblib", "metadata.json"):
                (temporary / name).unlink(missing_ok=True)
            temporary.rmdir()


def load_artifact(settings, record):
    if record["status"] != "completed":
        raise ArtifactError("Model run is not completed; inspect its status and retry training")
    directory = artifact_directory(settings, record["id"])
    pipeline, metadata_path = directory / "pipeline.joblib", directory / "metadata.json"
    registered_path = f"database://models/{record['id']}/pipeline.joblib" if settings.database_url else str(pipeline)
    if record["artifact_path"] != registered_path:
        raise ArtifactError("Model artifact is outside its registered application location")
    try:
        if settings.database_url:
            import io
            from app.db.models import ArtifactFile
            from app.db.session import Database
            db = Database(settings.runtime_dir / "restockiq.sqlite3", settings.database_url)
            try:
                with db.sessions() as session:
                    model_bytes = session.get(ArtifactFile, (record["id"], "pipeline.joblib")).payload
                    metadata_bytes = session.get(ArtifactFile, (record["id"], "metadata.json")).payload
            finally:
                db.engine.dispose()
            if (hashlib.sha256(model_bytes).hexdigest() != record["artifact_hash"] or
                    hashlib.sha256(metadata_bytes).hexdigest() != record["metadata_hash"]):
                raise ArtifactError("Model artifact integrity check failed; retrain this model")
            metadata = json.loads(metadata_bytes)
            if metadata["model_id"] != record["id"] or metadata["dataset_id"] != record["dataset_id"]:
                raise ArtifactError("Model metadata does not match its registered dataset/model")
            if metadata["feature_version"] != FEATURE_VERSION or metadata["dependencies"] != dependency_versions():
                raise ArtifactError("Model feature/dependency versions differ; retrain in this environment")
            candidate = joblib.load(io.BytesIO(model_bytes))
            if candidate["training_cutoff"] != record["training_cutoff"] or candidate["model_id"] != record["id"]:
                raise ArtifactError("Model payload does not match registered provenance")
            return candidate
        if pipeline.is_symlink() or metadata_path.is_symlink():
            raise ArtifactError("Symbolic model files are not supported")
        if digest(pipeline) != record["artifact_hash"] or digest(metadata_path) != record["metadata_hash"]:
            raise ArtifactError("Model artifact integrity check failed; retrain this model")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata["model_id"] != record["id"] or metadata["dataset_id"] != record["dataset_id"]:
            raise ArtifactError("Model metadata does not match its registered dataset/model")
        if metadata["feature_version"] != FEATURE_VERSION or metadata["dependencies"] != dependency_versions():
            raise ArtifactError("Model feature/dependency versions differ; retrain in this environment")
        candidate = joblib.load(pipeline)
        if candidate["training_cutoff"] != record["training_cutoff"] or candidate["model_id"] != record["id"]:
            raise ArtifactError("Model payload does not match registered provenance")
        return candidate
    except ArtifactError:
        raise
    except Exception as exc:
        raise ArtifactError("Model files are missing or unreadable; retrain this model") from exc
