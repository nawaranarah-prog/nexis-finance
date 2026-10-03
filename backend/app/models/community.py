"""Market-data cache, rate limiting, user accounts and the Finstagram social feed."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, utcnow


class MarketCache(Base):
    """Provider responses cached in the database (serverless instances share no memory)."""

    __tablename__ = "market_cache"

    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class RateEvent(Base):
    __tablename__ = "rate_events"
    __table_args__ = (Index("ix_rate_events_bucket_created", "bucket", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bucket: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(60), nullable=False)
    # Null for accounts that only sign in with Google/Apple.
    password_hash: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    # E.164 mobile number (e.g. +971501234567).
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)
    # Interface and advisor language: "en" or "ar".
    language: Mapped[str] = mapped_column(String(5), default="en", nullable=False)
    auth_provider: Mapped[str] = mapped_column(String(20), default="password", nullable=False)
    provider_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    # "person" or "page" (automated news pages run by the platform).
    kind: Mapped[str] = mapped_column(String(12), default="person", nullable=False)
    bio: Mapped[str | None] = mapped_column(String(300))
    # Links to the person's profiles elsewhere ({"instagram": "handle", "x": "handle", ...}).
    links: Mapped[dict | None] = mapped_column(JSON)
    avatar_media_id: Mapped[int | None] = mapped_column(Integer)
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class LinkedAccount(Base, TimestampMixin):
    """A Reddit or X identity the person proved they own by signing in there. Only the public identity is kept, no tokens."""

    __tablename__ = "linked_accounts"
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_linked_provider_user"),
        UniqueConstraint("user_id", "provider", name="uq_linked_user_provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(12), nullable=False)
    provider_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500))


class UserSession(Base, TimestampMixin):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Media(Base, TimestampMixin):
    __tablename__ = "media"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)


class Post(Base, TimestampMixin):
    __tablename__ = "posts"
    __table_args__ = (Index("ix_posts_created", "created_at"), Index("ix_posts_asset_created", "asset", "created_at"))

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"))
    # $CASHTAGS mentioned in the post, upper-cased (for the per-instrument feed).
    symbols: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    like_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comment_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Link card for posts that share an article.
    link_url: Mapped[str | None] = mapped_column(String(1500))
    link_title: Mapped[str | None] = mapped_column(String(500))
    link_source: Mapped[str | None] = mapped_column(String(160))
    link_image: Mapped[str | None] = mapped_column(String(1500))
    # De-duplication key for automatically imported articles.
    external_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    # ---- Nexis Pulse discussion fields (a discussion is a post about one asset, with a title)
    # Where the discussion comes from. Only "nexis" today; external sources get their own key later.
    source: Mapped[str] = mapped_column(String(16), default="nexis", server_default="nexis", nullable=False)
    asset: Mapped[str | None] = mapped_column(String(32))
    asset_name: Mapped[str | None] = mapped_column(String(160))
    title: Mapped[str | None] = mapped_column(String(160))
    # What the author chose ("bullish" / "neutral" / "bearish"), kept apart from what the AI detected.
    sentiment: Mapped[str | None] = mapped_column(String(8))
    ai_sentiment: Mapped[str | None] = mapped_column(String(8))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime)
    # The real event a Nexis-generated discussion is about (null for member posts).
    event_id: Mapped[int | None] = mapped_column(ForeignKey("source_events.id", ondelete="SET NULL"), index=True)


class PostTopic(Base):
    """Topics a discussion is about (earnings, valuation, …), indexed for trending-topic counts."""

    __tablename__ = "post_topics"

    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True)
    topic: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)


class Save(Base, TimestampMixin):
    __tablename__ = "saves"
    __table_args__ = (UniqueConstraint("post_id", "user_id", name="uq_saves_post_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)


class TopicFollow(Base, TimestampMixin):
    """Following an instrument ($EMAAR.AE) or a hashtag (#dubai) rather than an account."""

    __tablename__ = "topic_follows"
    __table_args__ = (UniqueConstraint("user_id", "kind", "value", name="uq_topic_follows_user_kind_value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    value: Mapped[str] = mapped_column(String(60), nullable=False)


class FeedPreference(Base):
    """Per-user ranking signals from "suggest more / less" (keys like ``sym:EMAAR.AE``, ``tag:dubai``, ``author:12``)."""

    __tablename__ = "feed_preferences"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_feed_preferences_user_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class Comment(Base, TimestampMixin):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    body: Mapped[str] = mapped_column(String(1000), nullable=False)
    # The comment this replies to (threads nest a few levels deep).
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("comments.id", ondelete="CASCADE"), index=True)
    # Written by a Nexis-generated persona rather than a member.
    generated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)


class Like(Base, TimestampMixin):
    __tablename__ = "likes"
    __table_args__ = (UniqueConstraint("post_id", "user_id", name="uq_likes_post_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)


class Follow(Base, TimestampMixin):
    __tablename__ = "follows"
    __table_args__ = (UniqueConstraint("follower_id", "followee_id", name="uq_follows_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    follower_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    followee_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)


class PostReport(Base, TimestampMixin):
    __tablename__ = "post_reports"
    __table_args__ = (UniqueConstraint("post_id", "user_id", name="uq_post_reports_post_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))
