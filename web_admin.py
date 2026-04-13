"""Web Admin Panel - Flask-based administration interface."""

import os
import logging
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from telegram import Bot

import config
from database import db

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-in-production")

# Initialize bot for sending messages
bot = Bot(token=config.BOT_TOKEN)


def admin_required(f):
    """Decorator to require admin authentication."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        admin_id = session.get("admin_id")
        if admin_id != config.ADMIN_USER_ID:
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated_function


@app.route("/")
def index():
    """Home page."""
    return redirect(url_for("login"))


@app.route("/login")
def login():
    """Login page."""
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login_post():
    """Handle login submission."""
    user_id = request.form.get("user_id", "")
    try:
        user_id = int(user_id)
    except ValueError:
        return render_template("login.html", error="Invalid user ID")

    if user_id == config.ADMIN_USER_ID:
        session["admin_id"] = user_id
        return redirect(url_for("dashboard"))

    return render_template("login.html", error="Not authorized")


@app.route("/logout")
def logout():
    """Logout."""
    session.pop("admin_id", None)
    return redirect(url_for("login"))


@app.route("/dashboard")
@admin_required
def dashboard():
    """Admin dashboard."""
    stats = db.get_stats()
    pending_count = db.get_pending_reviews_count()
    expired_albums = db.get_expired_albums()
    db_size = db.get_database_size()
    db_size_mb = db_size / (1024 * 1024)

    return render_template(
        "dashboard.html",
        stats=stats,
        pending_count=pending_count,
        expired_count=len(expired_albums),
        db_size_mb=db_size_mb,
    )


@app.route("/users")
@admin_required
def users():
    """User management page."""
    page = int(request.args.get("page", 0))
    page_size = 20
    offset = page * page_size

    all_users = db.get_all_users()
    total = len(all_users)
    users_page = all_users[offset : offset + page_size]

    # Enrich with stats
    for u in users_page:
        u["albums_count"] = db.get_user_albums_count(u["user_id"])
        u["media_count"] = db.get_user_media_count(u["user_id"])

    return render_template(
        "users.html", users=users_page, page=page, total=total, page_size=page_size
    )


@app.route("/users/<int:user_id>")
@admin_required
def user_detail(user_id):
    """User detail page."""
    user = db.get_user(user_id)
    if not user:
        return "User not found", 404

    albums_count = db.get_user_albums_count(user_id)
    media_count = db.get_user_media_count(user_id)
    total_views = db.get_user_total_views(user_id)
    albums = db.get_user_albums(user_id)

    return render_template(
        "user_detail.html",
        user=user,
        albums_count=albums_count,
        media_count=media_count,
        total_views=total_views,
        albums=albums,
    )


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def user_delete(user_id):
    """Delete user and all their content."""
    albums_deleted = db.delete_user_albums(user_id)
    media_deleted = db.delete_user_media(user_id)
    return jsonify(
        {
            "success": True,
            "albums_deleted": albums_deleted,
            "media_deleted": media_deleted,
        }
    )


@app.route("/albums")
@admin_required
def albums():
    """Album management page."""
    page = int(request.args.get("page", 0))
    page_size = 20
    offset = page * page_size

    all_albums = db.get_all_albums(limit=page_size, offset=offset)
    total = db.get_all_albums_count()

    return render_template(
        "albums.html", albums=all_albums, page=page, total=total, page_size=page_size
    )


@app.route("/albums/<int:album_id>/delete", methods=["POST"])
@admin_required
def album_delete(album_id):
    """Force delete album."""
    album = db.get_album(album_id)
    if not album:
        return jsonify({"success": False, "error": "Album not found"})

    db.force_delete_album(album_id)
    return jsonify({"success": True})


@app.route("/content")
@admin_required
def content():
    """Content management page."""
    status = request.args.get("status", "approved")
    page = int(request.args.get("page", 0))
    page_size = 20
    offset = page * page_size

    contents = db.get_media_by_status(status, limit=page_size, offset=offset)
    total = db.get_media_by_status_count(status)

    status_map = {"approved": "已发布", "rejected": "已拒绝", "pending": "待审核"}

    return render_template(
        "content.html",
        contents=contents,
        status=status,
        status_name=status_map.get(status, status),
        page=page,
        total=total,
        page_size=page_size,
    )


@app.route("/reviews")
@admin_required
def reviews():
    """Review queue page."""
    pending = db.get_all_pending_reviews()
    return render_template("reviews.html", reviews=pending)


@app.route("/reviews/<int:review_id>/approve", methods=["POST"])
@admin_required
def review_approve(review_id):
    """Approve a review."""
    review = db.get_pending_review(review_id)
    if not review:
        return jsonify({"success": False, "error": "Review not found"})

    # Publish to channel
    try:
        if review["file_type"] == "photo":
            msg = bot.send_photo(
                chat_id=config.PUBLIC_CHANNEL_ID,
                photo=review["file_id"],
                caption=f"{review['caption']}\n\n👤 @{review['username'] or review['first_name']}\n📁 {review['album_name']}",
            )
        elif review["file_type"] == "video":
            msg = bot.send_video(
                chat_id=config.PUBLIC_CHANNEL_ID,
                video=review["file_id"],
                caption=f"{review['caption']}\n\n👤 @{review['username'] or review['first_name']}\n📁 {review['album_name']}",
            )
        else:
            msg = bot.send_document(
                chat_id=config.PUBLIC_CHANNEL_ID,
                document=review["file_id"],
                caption=f"{review['caption']}\n\n👤 @{review['username'] or review['first_name']}\n📁 {review['album_name']}",
            )

        db.update_public_message_id(review["media_id"], msg.message_id)
        db.update_review_status(review_id, "approved", config.ADMIN_USER_ID)

        # Notify user
        bot.send_message(
            chat_id=review["user_id"], text="✅ 你的媒体已通过审核并发布到公开频道！"
        )

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Approve review failed: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route("/reviews/<int:review_id>/reject", methods=["POST"])
@admin_required
def review_reject(review_id):
    """Reject a review."""
    review = db.get_pending_review(review_id)
    if not review:
        return jsonify({"success": False, "error": "Review not found"})

    db.update_review_status(review_id, "rejected", config.ADMIN_USER_ID)

    # Notify user
    try:
        bot.send_message(
            chat_id=review["user_id"], text="❌ 你的媒体未通过审核，未发布到公开频道。"
        )
    except Exception:
        pass

    return jsonify({"success": True})


@app.route("/cleanup", methods=["POST"])
@admin_required
def cleanup_expired():
    """Cleanup expired albums."""
    count = db.cleanup_expired_albums()
    return jsonify({"success": True, "deleted": count})


@app.route("/api/stats")
@admin_required
def api_stats():
    """API endpoint for stats."""
    stats = db.get_stats()
    pending_count = db.get_pending_reviews_count()
    daily_stats = db.get_daily_stats(7)
    media_stats = db.get_media_daily_stats(7)
    db_size = db.get_database_size()

    return jsonify(
        {
            "stats": stats,
            "pending_count": pending_count,
            "daily_users": daily_stats,
            "daily_media": media_stats,
            "db_size_mb": db_size / (1024 * 1024),
        }
    )


def run_web_admin(host="0.0.0.0", port=5000, debug=False):
    """Run the web admin server."""
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_web_admin(debug=True)
