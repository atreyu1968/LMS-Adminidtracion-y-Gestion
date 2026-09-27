from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    code: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(250))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExternalIdentity(Base):
    __tablename__ = "external_identities"
    __table_args__ = (UniqueConstraint("issuer", "subject", name="uq_identity_issuer_subject"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    issuer: Mapped[str] = mapped_column(String(500))
    subject: Mapped[str] = mapped_column(String(500))
    client_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship()


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("platform_issuer", "context_id", name="uq_course_platform_context"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    platform_issuer: Mapped[str] = mapped_column(String(500))
    context_id: Mapped[str] = mapped_column(String(500))
    source_type: Mapped[str] = mapped_column(String(40), default="lti", index=True)
    title: Mapped[str] = mapped_column(String(300))
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    academic_year: Mapped[str | None] = mapped_column(String(40), nullable=True)
    join_code: Mapped[str | None] = mapped_column(String(40), nullable=True, unique=True)
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("course_id", "user_id", name="uq_membership_course_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(40), default="student")
    lti_roles: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Module(Base):
    __tablename__ = "modules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    module_type: Mapped[str] = mapped_column(String(40), default="scorm")
    version: Mapped[str] = mapped_column(String(40), default="0.1.0")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CourseModule(Base):
    __tablename__ = "course_modules"
    __table_args__ = (UniqueConstraint("course_id", "module_id", name="uq_course_module"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("modules.id", ondelete="CASCADE"), index=True)
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LTIPlatform(Base):
    __tablename__ = "lti_platforms"
    __table_args__ = (UniqueConstraint("issuer", "client_id", name="uq_lti_platform_client"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    issuer: Mapped[str] = mapped_column(String(500), index=True)
    client_id: Mapped[str] = mapped_column(String(500), index=True)
    auth_url: Mapped[str] = mapped_column(String(1000))
    token_url: Mapped[str] = mapped_column(String(1000))
    jwks_url: Mapped[str] = mapped_column(String(1000))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LTIDeployment(Base):
    __tablename__ = "lti_deployments"
    __table_args__ = (UniqueConstraint("platform_id", "deployment_id", name="uq_lti_deployment"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("lti_platforms.id", ondelete="CASCADE"), index=True)
    deployment_id: Mapped[str] = mapped_column(String(500))
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LTIState(Base):
    __tablename__ = "lti_states"

    state: Mapped[str] = mapped_column(String(180), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(180), unique=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("lti_platforms.id", ondelete="CASCADE"))
    target_link_uri: Mapped[str] = mapped_column(String(1500))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class LTIResourceLink(Base):
    __tablename__ = "lti_resource_links"
    __table_args__ = (
        UniqueConstraint("platform_id", "deployment_id", "resource_link_id", name="uq_lti_resource_link"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("lti_platforms.id", ondelete="CASCADE"), index=True)
    deployment_id: Mapped[str] = mapped_column(String(500))
    resource_link_id: Mapped[str] = mapped_column(String(500))
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    course_module_id: Mapped[int | None] = mapped_column(ForeignKey("course_modules.id"), nullable=True)
    learning_result_id: Mapped[int | None] = mapped_column(
        ForeignKey("learning_results.id", ondelete="SET NULL"), nullable=True, index=True
    )
    lineitem_url: Mapped[str | None] = mapped_column(String(1500), nullable=True)
    lineitems_url: Mapped[str | None] = mapped_column(String(1500), nullable=True)
    memberships_url: Mapped[str | None] = mapped_column(String(1500), nullable=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    last_launch_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GradeRecord(Base):
    __tablename__ = "grade_records"
    __table_args__ = (UniqueConstraint("resource_link_id", "user_id", name="uq_grade_resource_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resource_link_id: Mapped[int] = mapped_column(ForeignKey("lti_resource_links.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    score_given: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_maximum: Mapped[float] = mapped_column(Float, default=100.0)
    activity_progress: Mapped[str] = mapped_column(String(40), default="Completed")
    grading_progress: Mapped[str] = mapped_column(String(40), default="FullyGraded")
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScormPackage(Base):
    __tablename__ = "scorm_packages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id: Mapped[int | None] = mapped_column(ForeignKey("modules.id", ondelete="SET NULL"), nullable=True, index=True)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    original_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[str] = mapped_column(String(80), default="1")
    lineage_root_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    supersedes_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    revision_number: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    lifecycle_status: Mapped[str] = mapped_column(String(30), default="published", index=True)
    standard: Mapped[str] = mapped_column(String(40), default="SCORM_1.2")
    entrypoint: Mapped[str] = mapped_column(String(1000))
    storage_path: Mapped[str] = mapped_column(String(1000))
    sha256: Mapped[str] = mapped_column(String(64))
    manifest_json: Mapped[dict] = mapped_column(JSON, default=dict)
    visibility: Mapped[str] = mapped_column(String(30), default="private", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScormDraft(Base):
    __tablename__ = "scorm_drafts"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    base_package_id: Mapped[int] = mapped_column(ForeignKey("scorm_packages.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    working_path: Mapped[str] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(30), default="private")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModuleScormPackage(Base):
    __tablename__ = "module_scorm_packages"
    __table_args__ = (UniqueConstraint("module_id", "package_id", name="uq_module_scorm_package"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("modules.id", ondelete="CASCADE"), index=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("scorm_packages.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ScormRegistration(Base):
    __tablename__ = "scorm_registrations"
    __table_args__ = (UniqueConstraint("course_module_id", "package_id", "user_id", name="uq_scorm_registration"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_module_id: Mapped[int] = mapped_column(ForeignKey("course_modules.id", ondelete="CASCADE"))
    package_id: Mapped[int] = mapped_column(ForeignKey("scorm_packages.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    lesson_status: Mapped[str] = mapped_column(String(40), default="not attempted")
    score_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    lesson_location: Mapped[str] = mapped_column(String(500), default="")
    suspend_data: Mapped[str] = mapped_column(Text, default="")
    cmi_json: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModulePermission(Base):
    __tablename__ = "module_permissions"
    __table_args__ = (UniqueConstraint("module_id", "user_id", name="uq_module_permission"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("modules.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    permission: Mapped[str] = mapped_column(String(40), default="editor")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LTIDeepLinkRequest(Base):
    __tablename__ = "lti_deep_link_requests"

    token: Mapped[str] = mapped_column(String(180), primary_key=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("lti_platforms.id", ondelete="CASCADE"), index=True)
    deployment_id: Mapped[str] = mapped_column(String(500))
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    return_url: Mapped[str] = mapped_column(String(1500))
    data: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepts: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    original_filename: Mapped[str] = mapped_column(String(500))
    media_type: Mapped[str] = mapped_column(String(40), index=True)
    mime_type: Mapped[str] = mapped_column(String(160))
    storage_path: Mapped[str] = mapped_column(String(1000))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    visibility: Mapped[str] = mapped_column(String(30), default="private", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LearningResult(Base):
    __tablename__ = "learning_results"
    __table_args__ = (UniqueConstraint("module_id", "code", name="uq_learning_result_module_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("modules.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AssessmentCriterion(Base):
    __tablename__ = "assessment_criteria"
    __table_args__ = (UniqueConstraint("learning_result_id", "code", name="uq_criterion_lr_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    learning_result_id: Mapped[int] = mapped_column(
        ForeignKey("learning_results.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(500), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    pass_score: Mapped[float] = mapped_column(Float, default=5.0)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AssessmentItem(Base):
    __tablename__ = "assessment_items"
    __table_args__ = (
        UniqueConstraint("criterion_id", "instrument", "item_key", name="uq_assessment_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    criterion_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_criteria.id", ondelete="CASCADE"), index=True
    )
    instrument: Mapped[str] = mapped_column(String(40), index=True)
    item_key: Mapped[str] = mapped_column(String(160))
    item_type: Mapped[str] = mapped_column(String(40), default="choice")
    prompt: Mapped[str] = mapped_column(Text)
    options_json: Mapped[list] = mapped_column(JSON, default=list)
    public_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    evaluable: Mapped[bool] = mapped_column(Boolean, default=True)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1)
    position: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AssessmentKey(Base):
    __tablename__ = "assessment_keys"
    __table_args__ = (UniqueConstraint("item_id", name="uq_assessment_key_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("assessment_items.id", ondelete="CASCADE"), index=True)
    answer_json: Mapped[dict] = mapped_column(JSON, default=dict)
    feedback: Mapped[str] = mapped_column(Text, default="")
    public_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(80), default="private-bank")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvaluationConfig(Base):
    __tablename__ = "evaluation_configs"
    __table_args__ = (
        UniqueConstraint("course_module_id", "version", name="uq_eval_config_course_module_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_module_id: Mapped[int] = mapped_column(
        ForeignKey("course_modules.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssessmentAttempt(Base):
    __tablename__ = "assessment_attempts"
    __table_args__ = (
        UniqueConstraint(
            "course_module_id", "user_id", "item_id", "attempt_no",
            name="uq_assessment_attempt",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_module_id: Mapped[int] = mapped_column(
        ForeignKey("course_modules.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("assessment_items.id", ondelete="CASCADE"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="started", index=True)
    response_json: Mapped[dict] = mapped_column(JSON, default=dict)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pending_review: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssessmentReview(Base):
    __tablename__ = "assessment_reviews"
    __table_args__ = (UniqueConstraint("attempt_id", name="uq_assessment_review_attempt"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_attempts.id", ondelete="CASCADE"), index=True
    )
    ai_teacher_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(40), default="teacher", index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    proposed_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict: Mapped[str] = mapped_column(String(40), default="")
    feedback: Mapped[str] = mapped_column(Text, default="")
    breakdown_json: Mapped[list] = mapped_column(JSON, default=list)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    teacher_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    teacher_feedback: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExamSession(Base):
    __tablename__ = "exam_sessions"
    __table_args__ = (
        UniqueConstraint(
            "course_module_id", "user_id", "learning_result_id", "attempt_no",
            name="uq_exam_session",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_module_id: Mapped[int] = mapped_column(
        ForeignKey("course_modules.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    learning_result_id: Mapped[int] = mapped_column(
        ForeignKey("learning_results.id", ondelete="CASCADE"), index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(40), default="started", index=True)
    question_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    question_snapshot_json: Mapped[list] = mapped_column(JSON, default=list)
    response_json: Mapped[dict] = mapped_column(JSON, default=dict)
    security_events_json: Mapped[list] = mapped_column(JSON, default=list)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint(
            "course_module_id", "user_id", "learning_result_id",
            name="uq_evaluation_result",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_module_id: Mapped[int] = mapped_column(
        ForeignKey("course_modules.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    learning_result_id: Mapped[int] = mapped_column(
        ForeignKey("learning_results.id", ondelete="CASCADE"), index=True
    )
    portfolio_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    exam_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    criteria_passed: Mapped[int] = mapped_column(Integer, default=0)
    criteria_total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RecoveryPlan(Base):
    __tablename__ = "recovery_plans"
    __table_args__ = (
        UniqueConstraint(
            "course_module_id", "user_id", "learning_result_id",
            name="uq_recovery_plan",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_module_id: Mapped[int] = mapped_column(
        ForeignKey("course_modules.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    learning_result_id: Mapped[int] = mapped_column(
        ForeignKey("learning_results.id", ondelete="CASCADE"), index=True
    )
    criteria_json: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TeacherAISettings(Base):
    __tablename__ = "teacher_ai_settings"
    __table_args__ = (UniqueConstraint("user_id", name="uq_teacher_ai_settings_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[str] = mapped_column(String(80), default="openai-compatible")
    base_url: Mapped[str] = mapped_column(String(1000), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    encrypted_api_key: Mapped[str] = mapped_column(Text, default="")
    confidence_threshold: Mapped[float] = mapped_column(Float, default=0.75)
    auto_kinds_json: Mapped[list] = mapped_column(JSON, default=list)
    default_rubric: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CourseModuleAIConfig(Base):
    __tablename__ = "course_module_ai_configs"
    __table_args__ = (UniqueConstraint("course_module_id", name="uq_course_module_ai_config"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_module_id: Mapped[int] = mapped_column(
        ForeignKey("course_modules.id", ondelete="CASCADE"), index=True
    )
    teacher_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_review: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_kinds_json: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GuidedMilestoneProgress(Base):
    __tablename__ = "guided_milestone_progress"
    __table_args__ = (
        UniqueConstraint(
            "registration_id",
            "milestone_key",
            name="uq_guided_milestone_registration_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    registration_id: Mapped[int] = mapped_column(
        ForeignKey("scorm_registrations.id", ondelete="CASCADE"),
        index=True,
    )
    milestone_key: Mapped[str] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(40), default="not_started", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    ai_json: Mapped[dict] = mapped_column(JSON, default=dict)
    teacher_comment: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GuidedEvidence(Base):
    __tablename__ = "guided_evidence"
    __table_args__ = (
        UniqueConstraint(
            "registration_id",
            "milestone_key",
            "attempt_no",
            name="uq_guided_evidence_attempt",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    registration_id: Mapped[int] = mapped_column(
        ForeignKey("scorm_registrations.id", ondelete="CASCADE"),
        index=True,
    )
    milestone_key: Mapped[str] = mapped_column(String(160), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    original_filename: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[str] = mapped_column(String(160))
    storage_path: Mapped[str] = mapped_column(String(1000))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(40), default="submitted", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    ai_json: Mapped[dict] = mapped_column(JSON, default=dict)
    teacher_comment: Mapped[str] = mapped_column(Text, default="")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
