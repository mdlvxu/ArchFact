from app.domain.time import utc_now
from app.repositories.config_store import ConfigPersistence
from app.repositories.documents import DocumentPersistence
from app.repositories.gold import GoldPersistence
from app.repositories.jobs import JobPersistence
from app.repositories.outputs import OutputPersistence
from app.repositories.persistence import PersistenceOps
from app.repositories.quality import QualityPersistence
from app.repositories.rematch import RematchPersistence
from app.repositories.verification import VerificationPersistence

__all__ = ["MongoRepository", "utc_now"]


class MongoRepository(
    DocumentPersistence,
    GoldPersistence,
    QualityPersistence,
    ConfigPersistence,
    JobPersistence,
    OutputPersistence,
    RematchPersistence,
    VerificationPersistence,
    PersistenceOps,
):
    """Compatibility facade over bounded-context persistence mixins."""
