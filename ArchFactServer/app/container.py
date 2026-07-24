from dataclasses import dataclass

from app.core.config import Settings
from app.infrastructure.gridfs_storage import GridFsStorage
from app.infrastructure.local_image_storage import LocalImageStorage
from app.infrastructure.mongodb import MongoDatabase
from app.infrastructure.task_dispatcher import LocalJobDispatcher
from app.repositories.mongo_repository import MongoRepository
from app.services.artifact_entity_linker import ArtifactEntityLinker
from app.services.configuration_service import ConfigurationService
from app.services.detection_engine import build_detection_engine
from app.services.document_service import DocumentService
from app.services.document_text_index import DocumentTextIndexer
from app.services.extraction_engine import build_extraction_engine
from app.services.extraction_service import ExtractionService
from app.services.gold_dataset_service import GoldDatasetService
from app.services.image_service import ImageService
from app.services.ocr_engine import build_ocr_engine
from app.services.page_discovery import PageDiscoveryService
from app.services.page_preprocessor import PagePreprocessor
from app.services.pdf_parser import PdfParser
from app.services.post_processor import PostProcessor
from app.services.quality_evaluation_service import QualityEvaluationService
from app.services.region_processor import RegionProcessor
from app.services.relation_matcher import RelationMatcher, RelationMatcherConfig
from app.services.rematch_service import RematchService
from app.services.result_fusion import ResultFusionService
from app.services.verification_service import VerificationService


@dataclass(slots=True)
class Container:
    settings: Settings
    database: MongoDatabase
    repository: MongoRepository
    storage: GridFsStorage
    dispatcher: LocalJobDispatcher
    rematch_dispatcher: LocalJobDispatcher
    verification_dispatcher: LocalJobDispatcher
    quality_evaluation_dispatcher: LocalJobDispatcher
    document_service: DocumentService
    extraction_service: ExtractionService
    rematch_service: RematchService
    configuration_service: ConfigurationService
    image_service: ImageService
    gold_dataset_service: GoldDatasetService
    verification_service: VerificationService
    quality_evaluation_service: QualityEvaluationService


async def build_container(settings: Settings) -> Container:
    database = MongoDatabase(settings)
    await database.connect()
    repository = MongoRepository(database)
    configuration_service = ConfigurationService(repository)
    await configuration_service.seed_defaults()
    storage = GridFsStorage(database, settings)
    image_storage = LocalImageStorage(settings)
    ocr_engine = build_ocr_engine(settings)
    dispatcher = LocalJobDispatcher()
    rematch_dispatcher = LocalJobDispatcher()
    verification_dispatcher = LocalJobDispatcher()
    quality_evaluation_dispatcher = LocalJobDispatcher()
    relation_matcher = RelationMatcher(RelationMatcherConfig.from_settings(settings))
    result_fusion = ResultFusionService()
    entity_linker = ArtifactEntityLinker()
    extraction_service = ExtractionService(
        settings=settings,
        repository=repository,
        storage=storage,
        preprocessor=PagePreprocessor(
            settings=settings,
            parser=PdfParser(settings),
            repository=repository,
            image_storage=image_storage,
            ocr_engine=ocr_engine,
        ),
        engine=build_extraction_engine(settings),
        detector=build_detection_engine(settings),
        region_processor=RegionProcessor(
            settings=settings,
            image_storage=image_storage,
            ocr_engine=ocr_engine,
        ),
        relation_matcher=relation_matcher,
        result_fusion=result_fusion,
        entity_linker=entity_linker,
        document_text_indexer=DocumentTextIndexer(),
        page_discovery=PageDiscoveryService(settings, ocr_engine),
        post_processor=PostProcessor(),
        dispatcher=dispatcher,
    )
    dispatcher.bind(extraction_service.run_job)
    rematch_service = RematchService(
        repository=repository,
        relation_matcher=relation_matcher,
        result_fusion=result_fusion,
        entity_linker=entity_linker,
        dispatcher=rematch_dispatcher,
    )
    rematch_dispatcher.bind(rematch_service.run)
    verification_service = VerificationService(
        settings=settings,
        repository=repository,
        dispatcher=verification_dispatcher,
    )
    verification_dispatcher.bind(verification_service.run)
    quality_evaluation_service = QualityEvaluationService(
        repository=repository,
        dispatcher=quality_evaluation_dispatcher,
    )
    quality_evaluation_dispatcher.bind(quality_evaluation_service.run)
    return Container(
        settings=settings,
        database=database,
        repository=repository,
        storage=storage,
        dispatcher=dispatcher,
        rematch_dispatcher=rematch_dispatcher,
        verification_dispatcher=verification_dispatcher,
        quality_evaluation_dispatcher=quality_evaluation_dispatcher,
        document_service=DocumentService(repository, storage),
        extraction_service=extraction_service,
        rematch_service=rematch_service,
        configuration_service=configuration_service,
        image_service=ImageService(repository, storage, image_storage),
        gold_dataset_service=GoldDatasetService(settings, repository),
        verification_service=verification_service,
        quality_evaluation_service=quality_evaluation_service,
    )
