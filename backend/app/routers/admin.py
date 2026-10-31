"""Admin analytics API.

Every number here is computed from the existing database on each request — nothing is
hardcoded and nothing is cached. Every route requires an authenticated admin
(`require_admin`); a teacher gets a 403 even if they somehow reach the URL directly.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.database.session import get_db
from app.models.models import (
    ActivityType,
    LearningActivity,
    Lesson,
    LessonMedia,
    Role,
    SpeakingAttempt,
    User,
    UserLessonProgress,
    UserLogin,
)
from app.schemas.contracts import (
    AdminCompletionStats,
    AdminLoginStats,
    AdminMediaStats,
    AdminOverview,
    AdminUserStats,
    DailyLoginPoint,
    RecentActivityItem,
    TopMediaItem,
)
from app.services.activity_service import today_local

router = APIRouter(prefix="/admin/stats", tags=["admin-stats"])

LOGIN_HISTORY_DAYS = 14  # how many days of daily-login history to return
RECENT_ACTIVITY_LIMIT = 10
TOP_MEDIA_LIMIT = 5


# ---------------------------------------------------------------------------
# 1. User statistics
# ---------------------------------------------------------------------------
def _user_stats(db: Session) -> AdminUserStats:
    now = datetime.utcnow()
    cut_7 = now - timedelta(days=7)
    cut_30 = now - timedelta(days=30)

    row = db.query(
        func.count(User.id),
        func.coalesce(func.sum(case((User.role == Role.TEACHER, 1), else_=0)), 0),
        func.coalesce(func.sum(case((User.role == Role.ADMIN, 1), else_=0)), 0),
        func.coalesce(func.sum(case((User.is_active.is_(True), 1), else_=0)), 0),
        func.coalesce(func.sum(case((User.is_active.is_(False), 1), else_=0)), 0),
        func.coalesce(func.sum(case((User.created_at >= cut_7, 1), else_=0)), 0),
        func.coalesce(func.sum(case((User.created_at >= cut_30, 1), else_=0)), 0),
    ).one()

    total_users = int(row[0] or 0)
    total_teachers = int(row[1] or 0)
    total_admins = int(row[2] or 0)
    active_users = int(row[3] or 0)
    inactive_users = int(row[4] or 0)
    new_7 = int(row[5] or 0)
    new_30 = int(row[6] or 0)

    users_logged_in = db.query(func.count(func.distinct(UserLogin.user_id))).scalar() or 0
    users_never_logged_in = max(0, total_users - users_logged_in)

    return AdminUserStats(
        total_users=total_users,
        total_teachers=total_teachers,
        total_admins=total_admins,
        active_users=active_users,
        inactive_users=inactive_users,
        users_logged_in=users_logged_in,
        users_never_logged_in=users_never_logged_in,
        new_users_last_7_days=new_7,
        new_users_last_30_days=new_30,
    )


# ---------------------------------------------------------------------------
# 2. Daily login statistics
# ---------------------------------------------------------------------------
def _login_stats(db: Session) -> AdminLoginStats:
    today = today_local()
    yesterday = today - timedelta(days=1)
    since_7 = today - timedelta(days=6)  # inclusive of today = 7 days
    since_30 = today - timedelta(days=29)
    since_history = today - timedelta(days=LOGIN_HISTORY_DAYS - 1)

    rows = (
        db.query(UserLogin.login_date, func.count(UserLogin.id))
        .filter(UserLogin.login_date >= since_30, UserLogin.login_date <= today)
        .group_by(UserLogin.login_date)
        .all()
    )
    counts_by_day = {d: int(c) for d, c in rows}
    today_count = counts_by_day.get(today, 0)
    yesterday_count = counts_by_day.get(yesterday, 0)
    last_7_days = sum(c for d, c in counts_by_day.items() if since_7 <= d <= today)
    last_30_days = sum(c for d, c in counts_by_day.items() if since_30 <= d <= today)

    history = [
        DailyLoginPoint(date=since_history + timedelta(days=i), count=counts_by_day.get(since_history + timedelta(days=i), 0))
        for i in range(LOGIN_HISTORY_DAYS)
    ]

    return AdminLoginStats(
        today=today_count,
        yesterday=yesterday_count,
        last_7_days=last_7_days,
        last_30_days=last_30_days,
        daily_history=history,
    )


# ---------------------------------------------------------------------------
# 3. Learning completion statistics
# ---------------------------------------------------------------------------
def _completion_stats(db: Session, *, total_users: int | None = None) -> AdminCompletionStats:
    if total_users is None:
        total_users = db.query(func.count(User.id)).scalar() or 0
    total_published_lessons = db.query(func.count(Lesson.id)).filter(Lesson.published.is_(True)).scalar() or 0

    users_started = db.query(func.count(func.distinct(LearningActivity.user_id))).scalar() or 0

    lp_row = (
        db.query(
            func.count(func.distinct(UserLessonProgress.user_id)),
            func.count(UserLessonProgress.id),
        )
        .filter(UserLessonProgress.completed.is_(True))
        .one()
    )
    users_completed_lesson = int(lp_row[0] or 0)
    lessons_completed_total = int(lp_row[1] or 0)

    att_row = db.query(
        func.count(SpeakingAttempt.id),
        func.coalesce(func.sum(case((SpeakingAttempt.analysis_status == "completed", 1), else_=0)), 0),
        func.count(func.distinct(case((SpeakingAttempt.analysis_status == "completed", SpeakingAttempt.user_id), else_=None))),
    ).one()
    practice_attempts_total = int(att_row[0] or 0)
    practice_attempts_completed = int(att_row[1] or 0)
    users_completed_practice = int(att_row[2] or 0)

    completion_rate = None
    if total_users:
        completion_rate = round(users_completed_lesson / total_users * 100, 1)

    return AdminCompletionStats(
        users_started_learning=users_started,
        users_completed_a_lesson=users_completed_lesson,
        lessons_completed_total=lessons_completed_total,
        users_completed_practice=users_completed_practice,
        practice_attempts_completed=practice_attempts_completed,
        practice_attempts_total=practice_attempts_total,
        total_published_lessons=total_published_lessons,
        completion_rate_percent=completion_rate,
    )


# ---------------------------------------------------------------------------
# 4. Podcast / media statistics
# ---------------------------------------------------------------------------
def _media_stats(db: Session) -> AdminMediaStats:
    media_row = db.query(
        func.count(LessonMedia.id),
        func.coalesce(func.sum(case((LessonMedia.media_type == "video", 1), else_=0)), 0),
        func.coalesce(func.sum(case((LessonMedia.media_type == "audio", 1), else_=0)), 0),
        func.coalesce(func.sum(case((LessonMedia.media_type.in_(["image", "thumbnail"]), 1), else_=0)), 0),
        func.count(func.distinct(LessonMedia.lesson_id)),
    ).one()
    total_media = int(media_row[0] or 0)
    total_videos = int(media_row[1] or 0)
    total_audio = int(media_row[2] or 0)
    total_images = int(media_row[3] or 0)
    lessons_with_media = int(media_row[4] or 0)

    # "Accessed" media = a logged video/podcast learning activity (the only reliable,
    # already-tracked signal for media engagement in the existing schema).
    act_row = (
        db.query(
            func.count(func.distinct(LearningActivity.user_id)),
            func.coalesce(func.sum(case((LearningActivity.activity_type == ActivityType.VIDEO.value, 1), else_=0)), 0),
            func.coalesce(func.sum(case((LearningActivity.activity_type == ActivityType.PODCAST.value, 1), else_=0)), 0),
        )
        .filter(LearningActivity.activity_type.in_([ActivityType.VIDEO.value, ActivityType.PODCAST.value]))
        .one()
    )
    users_accessed = int(act_row[0] or 0)
    video_activity = int(act_row[1] or 0)
    podcast_activity = int(act_row[2] or 0)

    top_rows = (
        db.query(LearningActivity.lesson_id, LearningActivity.activity_type, func.count(LearningActivity.id).label("n"))
        .filter(
            LearningActivity.activity_type.in_([ActivityType.VIDEO.value, ActivityType.PODCAST.value]),
            LearningActivity.lesson_id.isnot(None),
        )
        .group_by(LearningActivity.lesson_id, LearningActivity.activity_type)
        .order_by(func.count(LearningActivity.id).desc())
        .limit(TOP_MEDIA_LIMIT)
        .all()
    )
    lesson_ids = [r[0] for r in top_rows]
    titles = {}
    if lesson_ids:
        titles = {lid: title for lid, title in db.query(Lesson.id, Lesson.title).filter(Lesson.id.in_(lesson_ids)).all()}
    most_used = [
        TopMediaItem(
            lesson_id=lesson_id,
            lesson_title=titles.get(lesson_id, f"Lesson #{lesson_id}"),
            media_type=activity_type,
            times_accessed=count,
        )
        for lesson_id, activity_type, count in top_rows
    ]

    return AdminMediaStats(
        total_media=total_media,
        total_videos=total_videos,
        total_audio=total_audio,
        total_images=total_images,
        lessons_with_media=lessons_with_media,
        users_who_accessed_media=users_accessed,
        video_activity_count=video_activity,
        podcast_activity_count=podcast_activity,
        most_used_media=most_used,
    )


# ---------------------------------------------------------------------------
# 5. Recent activity (real practice attempts, not sample data)
# ---------------------------------------------------------------------------
def _recent_activity(db: Session) -> list[RecentActivityItem]:
    rows = (
        db.query(SpeakingAttempt, User, Lesson)
        .join(User, SpeakingAttempt.user_id == User.id)
        .outerjoin(Lesson, SpeakingAttempt.lesson_id == Lesson.id)
        .order_by(SpeakingAttempt.created_at.desc(), SpeakingAttempt.id.desc())
        .limit(RECENT_ACTIVITY_LIMIT)
        .all()
    )
    return [
        RecentActivityItem(
            user_name=user.full_name,
            activity="Practice recording",
            detail=lesson.title if lesson else "Free practice",
            score=attempt.overall_score,
            created_at=attempt.created_at,
        )
        for attempt, user, lesson in rows
    ]


@router.get("/overview", response_model=AdminOverview)
def get_overview(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    """Every statistic on the admin dashboard, computed live from the database."""
    users = _user_stats(db)
    return AdminOverview(
        users=users,
        logins=_login_stats(db),
        completion=_completion_stats(db, total_users=users.total_users),
        media=_media_stats(db),
        recent_activity=_recent_activity(db),
    )
