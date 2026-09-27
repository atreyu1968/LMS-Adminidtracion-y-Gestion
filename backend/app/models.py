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
    standard: Mapped[str] = mapped_column(String(40), default="SCORM_1.2")
    entrypoint: Mapped[str] = mapped_column(String(1000))
    storage_path: Mapped[str] = mapped_column(String(1000))
    sha256: Mapped[str] = mapped_column(String(64))
    manifest_json: Mapped[dict] = mapped_column(JSON, default=dict)
    visibility: Mapped[str] = mapped_column(String(30), default="private", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
