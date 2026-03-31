import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "coop_support.db"
UPLOADS_DIR = BASE_DIR / "uploads" / "work_terms"
MAX_REPORT_SIZE_BYTES = 10 * 1024 * 1024
WORK_TERM_DEADLINE = date(2026, 4, 15)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "sprint2-group43-dev-secret"
    app.config["DATABASE"] = str(DB_PATH)

    @app.before_request
    def load_logged_in_user() -> None:
        user_id = session.get("user_id")
        g.user = None
        g.unread_notifications = 0
        if user_id is not None:
            g.user = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
            unread = query_one(
                "SELECT COUNT(*) AS total FROM notifications WHERE user_id = ? AND is_read = 0",
                (user_id,),
            )
            g.unread_notifications = unread["total"] if unread else 0

    @app.teardown_appcontext
    def close_db_connection(_exception=None) -> None:
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def get_db() -> sqlite3.Connection:
        if "db" not in g:
            db = sqlite3.connect(app.config["DATABASE"])
            db.row_factory = sqlite3.Row
            g.db = db
        return g.db

    def execute(query: str, params: tuple = ()) -> sqlite3.Cursor:
        cursor = get_db().execute(query, params)
        get_db().commit()
        return cursor

    def query_all(query: str, params: tuple = ()) -> list[sqlite3.Row]:
        return list(get_db().execute(query, params).fetchall())

    def query_one(query: str, params: tuple = ()) -> sqlite3.Row | None:
        return get_db().execute(query, params).fetchone()

    def table_columns(table_name: str) -> set[str]:
        rows = query_all(f"PRAGMA table_info({table_name})")
        return {row["name"] for row in rows}

    def ensure_column(table_name: str, column_name: str, definition: str) -> None:
        if column_name not in table_columns(table_name):
            execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def create_notification(user_id: int, message: str, kind: str = "info", link: str | None = None) -> None:
        execute(
            "INSERT INTO notifications(user_id, message, kind, link) VALUES (?, ?, ?, ?)",
            (user_id, message, kind, link),
        )

    def create_daily_notification_once(user_id: int, message: str, kind: str = "info") -> None:
        already = query_one(
            """
            SELECT id
            FROM notifications
            WHERE user_id = ?
              AND message = ?
              AND DATE(created_at) = DATE('now', 'localtime')
            LIMIT 1
            """,
            (user_id, message),
        )
        if already is None:
            create_notification(user_id, message, kind)

    def build_recommendation_scores(profile: sqlite3.Row | None, jobs: list[sqlite3.Row]) -> list[dict]:
        tokens: set[str] = set()
        if profile:
            for key in ("degree", "program", "institution"):
                raw = (profile[key] or "").lower()
                for part in raw.replace("/", " ").replace(",", " ").split():
                    if len(part) > 2:
                        tokens.add(part)

        ranked: list[dict] = []
        for job in jobs:
            content = f"{job['title']} {job['description']} {job['requirements']}".lower()
            score = 0
            for token in tokens:
                if token in content:
                    score += 1
            ranked.append({"job": job, "score": score})

        ranked.sort(key=lambda item: (item["score"], item["job"]["deadline"]), reverse=True)
        return ranked

    def login_required(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if g.user is None:
                flash("Please log in first.")
                return redirect(url_for("login"))
            return view(*args, **kwargs)

        return wrapped_view

    def role_required(*roles):
        def decorator(view):
            @wraps(view)
            def wrapped_view(*args, **kwargs):
                if g.user is None:
                    flash("Please log in first.")
                    return redirect(url_for("login"))
                if g.user["role"] not in roles:
                    flash("You are not allowed to access that page.")
                    return redirect(url_for("dashboard"))
                return view(*args, **kwargs)

            return wrapped_view

        return decorator

    def init_db() -> None:
        execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('student', 'recruiter', 'coordinator')),
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS student_profiles (
                user_id INTEGER PRIMARY KEY,
                institution TEXT,
                degree TEXT,
                program TEXT,
                grades TEXT,
                resume_text TEXT,
                work_term_completed INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recruiter_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                requirements TEXT NOT NULL,
                deadline TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (recruiter_id) REFERENCES users(id)
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                cover_letter TEXT,
                transcript_notes TEXT,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'accepted', 'rejected', 'withdrawn')),
                applied_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(job_id, student_id),
                FOREIGN KEY (job_id) REFERENCES jobs(id),
                FOREIGN KEY (student_id) REFERENCES users(id)
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                sent_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sender_id) REFERENCES users(id),
                FOREIGN KEY (receiver_id) REFERENCES users(id)
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                job_id INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, job_id),
                FOREIGN KEY (student_id) REFERENCES users(id),
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'info',
                link TEXT,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS work_term_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES users(id)
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS two_factor_settings (
                user_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS shortlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recruiter_id INTEGER NOT NULL,
                application_id INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(recruiter_id, application_id),
                FOREIGN KEY (recruiter_id) REFERENCES users(id),
                FOREIGN KEY (application_id) REFERENCES applications(id)
            )
            """
        )

        # Keep compatibility with old Sprint 1 databases.
        ensure_column("applications", "transcript_notes", "TEXT")

        coordinator = query_one("SELECT id FROM users WHERE role = 'coordinator' LIMIT 1")
        if coordinator is None:
            execute(
                """
                INSERT INTO users(username, email, password_hash, role)
                VALUES (?, ?, ?, 'coordinator')
                """,
                (
                    "coord1",
                    "coord1@example.com",
                    generate_password_hash("coord123"),
                ),
            )

        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            role = request.form.get("role", "student")

            if role not in {"student", "recruiter"}:
                flash("Invalid role selected.")
                return redirect(url_for("register"))
            if not username or not email or len(password) < 6:
                flash("Username, email and password (min 6 chars) are required.")
                return redirect(url_for("register"))

            try:
                cursor = execute(
                    "INSERT INTO users(username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    (username, email, generate_password_hash(password), role),
                )
                user_id = cursor.lastrowid
                execute("INSERT INTO two_factor_settings(user_id, enabled) VALUES (?, 0)", (user_id,))
                flash("Account created. Please log in.")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("Username or email already exists.")

        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = query_one("SELECT * FROM users WHERE username = ?", (username,))

            if user is None or not check_password_hash(user["password_hash"], password):
                flash("Invalid username or password.")
                return redirect(url_for("login"))

            two_fa = query_one(
                "SELECT enabled FROM two_factor_settings WHERE user_id = ?",
                (user["id"],),
            )
            if two_fa and two_fa["enabled"]:
                code = f"{secrets.randbelow(1000000):06d}"
                session["pending_2fa_user_id"] = user["id"]
                session["pending_2fa_code"] = code
                session["pending_2fa_expires"] = (
                    datetime.now() + timedelta(minutes=5)
                ).isoformat(timespec="seconds")
                flash(f"Your demo 2FA code is: {code}")
                return redirect(url_for("verify_2fa"))

            session.clear()
            session["user_id"] = user["id"]
            flash("Logged in successfully.")
            return redirect(url_for("dashboard"))

        return render_template("login.html")

    @app.route("/verify-2fa", methods=["GET", "POST"])
    def verify_2fa():
        pending_user_id = session.get("pending_2fa_user_id")
        pending_code = session.get("pending_2fa_code")
        pending_expires = session.get("pending_2fa_expires")

        if not pending_user_id or not pending_code or not pending_expires:
            flash("2FA session expired. Please log in again.")
            return redirect(url_for("login"))

        if request.method == "POST":
            submitted = request.form.get("code", "").strip()
            expires_dt = datetime.fromisoformat(pending_expires)
            if datetime.now() > expires_dt:
                session.pop("pending_2fa_user_id", None)
                session.pop("pending_2fa_code", None)
                session.pop("pending_2fa_expires", None)
                flash("2FA code expired. Please log in again.")
                return redirect(url_for("login"))
            if submitted != pending_code:
                flash("Invalid code.")
                return redirect(url_for("verify_2fa"))

            session.clear()
            session["user_id"] = pending_user_id
            flash("Logged in successfully with 2FA.")
            return redirect(url_for("dashboard"))

        return render_template("verify_2fa.html")

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            user = query_one("SELECT id FROM users WHERE email = ?", (email,))
            if user:
                token = secrets.token_urlsafe(24)
                expires = (datetime.now() + timedelta(minutes=30)).isoformat(timespec="seconds")
                execute(
                    "INSERT INTO password_reset_tokens(user_id, token, expires_at) VALUES (?, ?, ?)",
                    (user["id"], token, expires),
                )
                reset_link = url_for("reset_password", token=token)
                flash(f"Password reset link generated (demo): {reset_link}")
            else:
                flash("If that email exists, a reset link has been generated.")
            return redirect(url_for("login"))

        return render_template("forgot_password.html")

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token: str):
        row = query_one(
            """
            SELECT password_reset_tokens.*, users.username
            FROM password_reset_tokens
            JOIN users ON users.id = password_reset_tokens.user_id
            WHERE token = ?
            """,
            (token,),
        )
        if row is None:
            flash("Invalid reset token.")
            return redirect(url_for("login"))

        if row["used"]:
            flash("This reset token has already been used.")
            return redirect(url_for("login"))

        if datetime.now() > datetime.fromisoformat(row["expires_at"]):
            flash("Reset token has expired.")
            return redirect(url_for("login"))

        if request.method == "POST":
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")
            if len(password) < 6 or password != confirm_password:
                flash("Passwords must match and be at least 6 characters.")
                return redirect(url_for("reset_password", token=token))

            execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(password), row["user_id"]),
            )
            execute("UPDATE password_reset_tokens SET used = 1 WHERE id = ?", (row["id"],))
            flash("Password reset complete. Please log in.")
            return redirect(url_for("login"))

        return render_template("reset_password.html", token=token, username=row["username"])

    @app.route("/logout")
    def logout():
        session.clear()
        flash("You have been logged out.")
        return redirect(url_for("index"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        if g.user["role"] == "student":
            profile = query_one("SELECT * FROM student_profiles WHERE user_id = ?", (g.user["id"],))
            jobs = query_all(
                """
                SELECT jobs.*, users.username AS recruiter_name
                FROM jobs
                JOIN users ON users.id = jobs.recruiter_id
                WHERE jobs.deadline >= ?
                ORDER BY jobs.deadline ASC
                """,
                (str(date.today()),),
            )
            ranked = build_recommendation_scores(profile, jobs)
            recommendations = [item["job"] for item in ranked if item["score"] > 0][:3]

            if recommendations:
                create_daily_notification_once(
                    g.user["id"],
                    f"You have {len(recommendations)} recommended jobs available.",
                    "recommendation",
                )

            due_soon = query_all(
                """
                SELECT jobs.title, jobs.deadline
                FROM applications
                JOIN jobs ON jobs.id = applications.job_id
                WHERE applications.student_id = ?
                  AND applications.status IN ('pending', 'accepted')
                  AND DATE(jobs.deadline) BETWEEN DATE('now', 'localtime') AND DATE('now', '+3 day', 'localtime')
                """,
                (g.user["id"],),
            )
            for item in due_soon:
                create_daily_notification_once(
                    g.user["id"],
                    f"Upcoming deadline: {item['title']} due on {item['deadline']}.",
                    "deadline",
                )

            return render_template(
                "student_dashboard.html",
                profile=profile,
                recommendations=recommendations,
            )

        if g.user["role"] == "recruiter":
            metrics = query_one(
                """
                SELECT
                    (SELECT COUNT(*) FROM jobs WHERE recruiter_id = ?) AS job_count,
                    (SELECT COUNT(*)
                     FROM applications
                     JOIN jobs ON jobs.id = applications.job_id
                     WHERE jobs.recruiter_id = ?) AS application_count,
                    (SELECT COUNT(*) FROM shortlists WHERE recruiter_id = ?) AS shortlist_count
                """,
                (g.user["id"], g.user["id"], g.user["id"]),
            )
            return render_template("recruiter_dashboard.html", metrics=metrics)

        student_count = query_one("SELECT COUNT(*) AS total FROM users WHERE role = 'student'")
        outcome_counts = query_all(
            """
            SELECT status, COUNT(*) AS total
            FROM applications
            GROUP BY status
            ORDER BY status
            """
        )
        return render_template(
            "coordinator_dashboard.html",
            student_count=student_count["total"],
            outcome_counts=outcome_counts,
        )

    @app.route("/security", methods=["GET", "POST"])
    @login_required
    def security_settings():
        if request.method == "POST":
            enabled = 1 if request.form.get("two_factor_enabled") == "on" else 0
            existing = query_one(
                "SELECT user_id FROM two_factor_settings WHERE user_id = ?",
                (g.user["id"],),
            )
            if existing:
                execute("UPDATE two_factor_settings SET enabled = ? WHERE user_id = ?", (enabled, g.user["id"]))
            else:
                execute(
                    "INSERT INTO two_factor_settings(user_id, enabled) VALUES (?, ?)",
                    (g.user["id"], enabled),
                )
            flash("Security settings saved.")
            return redirect(url_for("security_settings"))

        two_fa = query_one("SELECT enabled FROM two_factor_settings WHERE user_id = ?", (g.user["id"],))
        return render_template("security.html", two_fa_enabled=bool(two_fa and two_fa["enabled"]))

    @app.route("/student/profile", methods=["GET", "POST"])
    @role_required("student")
    def student_profile():
        if request.method == "POST":
            institution = request.form.get("institution", "").strip()
            degree = request.form.get("degree", "").strip()
            program = request.form.get("program", "").strip()
            grades = request.form.get("grades", "").strip()
            resume_text = request.form.get("resume_text", "").strip()
            work_term_completed = 1 if request.form.get("work_term_completed") == "on" else 0

            exists = query_one("SELECT user_id FROM student_profiles WHERE user_id = ?", (g.user["id"],))
            if exists:
                execute(
                    """
                    UPDATE student_profiles
                    SET institution = ?, degree = ?, program = ?, grades = ?, resume_text = ?, work_term_completed = ?
                    WHERE user_id = ?
                    """,
                    (
                        institution,
                        degree,
                        program,
                        grades,
                        resume_text,
                        work_term_completed,
                        g.user["id"],
                    ),
                )
            else:
                execute(
                    """
                    INSERT INTO student_profiles(user_id, institution, degree, program, grades, resume_text, work_term_completed)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        g.user["id"],
                        institution,
                        degree,
                        program,
                        grades,
                        resume_text,
                        work_term_completed,
                    ),
                )

            profile = query_one("SELECT * FROM student_profiles WHERE user_id = ?", (g.user["id"],))
            all_jobs = query_all("SELECT title, description, requirements, deadline FROM jobs")
            ranked = build_recommendation_scores(profile, all_jobs)
            if ranked and ranked[0]["score"] > 0:
                create_daily_notification_once(
                    g.user["id"],
                    "Your profile update changed recommended jobs.",
                    "recommendation",
                )

            flash("Profile saved.")
            return redirect(url_for("student_profile"))

        profile = query_one("SELECT * FROM student_profiles WHERE user_id = ?", (g.user["id"],))

        resume_feedback = None
        if profile and profile["resume_text"]:
            keywords = {"python", "sql", "communication", "teamwork", "analysis", "flask"}
            text = profile["resume_text"].lower()
            found = sorted(word for word in keywords if word in text)
            missing = sorted(keywords - set(found))
            resume_feedback = {
                "found": found,
                "missing": missing,
                "score": int((len(found) / max(len(keywords), 1)) * 100),
            }

        return render_template("student_profile.html", profile=profile, resume_feedback=resume_feedback)

    @app.route("/student/jobs")
    @role_required("student")
    def student_jobs():
        q = request.args.get("q", "").strip().lower()
        deadline_before = request.args.get("deadline_before", "").strip()
        saved_only = request.args.get("saved") == "1"
        today = str(date.today())

        jobs = query_all(
            """
            SELECT jobs.*, users.username AS recruiter_name
            FROM jobs
            JOIN users ON users.id = jobs.recruiter_id
            WHERE jobs.deadline >= ?
            ORDER BY jobs.deadline ASC
            """,
            (today,),
        )

        profile = query_one("SELECT * FROM student_profiles WHERE user_id = ?", (g.user["id"],))
        ranked = build_recommendation_scores(profile, jobs)

        bookmarks = query_all("SELECT job_id FROM bookmarks WHERE student_id = ?", (g.user["id"],))
        bookmarked_ids = {row["job_id"] for row in bookmarks}

        filtered: list[dict] = []
        for item in ranked:
            job = item["job"]
            content = f"{job['title']} {job['description']} {job['requirements']}".lower()
            if q and q not in content:
                continue
            if deadline_before and job["deadline"] > deadline_before:
                continue
            if saved_only and job["id"] not in bookmarked_ids:
                continue

            filtered.append(
                {
                    "job": job,
                    "score": item["score"],
                    "bookmarked": job["id"] in bookmarked_ids,
                }
            )

        return render_template(
            "student_jobs.html",
            jobs=filtered,
            q=q,
            deadline_before=deadline_before,
            saved_only=saved_only,
        )

    @app.route("/student/jobs/<int:job_id>/bookmark", methods=["POST"])
    @role_required("student")
    def bookmark_job(job_id: int):
        try:
            execute(
                "INSERT INTO bookmarks(student_id, job_id) VALUES (?, ?)",
                (g.user["id"], job_id),
            )
            flash("Job bookmarked.")
        except sqlite3.IntegrityError:
            execute(
                "DELETE FROM bookmarks WHERE student_id = ? AND job_id = ?",
                (g.user["id"], job_id),
            )
            flash("Bookmark removed.")
        return redirect(url_for("student_jobs"))

    @app.route("/student/jobs/<int:job_id>/apply", methods=["POST"])
    @role_required("student")
    def apply_job(job_id: int):
        cover_letter = request.form.get("cover_letter", "").strip()
        transcript_notes = request.form.get("transcript_notes", "").strip()

        try:
            execute(
                """
                INSERT INTO applications(job_id, student_id, cover_letter, transcript_notes, status)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (job_id, g.user["id"], cover_letter, transcript_notes),
            )
            flash("Application submitted.")
        except sqlite3.IntegrityError:
            flash("You already applied to this job.")

        return redirect(url_for("student_applications"))

    @app.route("/student/applications")
    @role_required("student")
    def student_applications():
        rows = query_all(
            """
            SELECT applications.*, jobs.title, jobs.deadline
            FROM applications
            JOIN jobs ON jobs.id = applications.job_id
            WHERE applications.student_id = ?
            ORDER BY applications.applied_at DESC
            """,
            (g.user["id"],),
        )
        return render_template("student_applications.html", applications=rows)

    @app.route("/student/applications/<int:application_id>/withdraw", methods=["POST"])
    @role_required("student")
    def withdraw_application(application_id: int):
        execute(
            "UPDATE applications SET status = 'withdrawn' WHERE id = ? AND student_id = ?",
            (application_id, g.user["id"]),
        )
        flash("Application withdrawn.")
        return redirect(url_for("student_applications"))

    @app.route("/student/work-term", methods=["GET", "POST"])
    @role_required("student")
    def student_work_term():
        if request.method == "POST":
            if date.today() > WORK_TERM_DEADLINE:
                flash("Work-term upload window is closed.")
                return redirect(url_for("student_work_term"))

            report_file = request.files.get("report")
            if not report_file or not report_file.filename:
                flash("Please select a file.")
                return redirect(url_for("student_work_term"))

            safe_name = secure_filename(report_file.filename)
            if not safe_name.lower().endswith(".pdf"):
                flash("Only PDF files are allowed.")
                return redirect(url_for("student_work_term"))

            report_file.seek(0, os.SEEK_END)
            size = report_file.tell()
            report_file.seek(0)
            if size > MAX_REPORT_SIZE_BYTES:
                flash("File must be smaller than 10MB.")
                return redirect(url_for("student_work_term"))

            saved_name = f"student_{g.user['id']}_{int(datetime.now().timestamp())}.pdf"
            save_path = UPLOADS_DIR / saved_name
            report_file.save(save_path)

            execute(
                """
                INSERT INTO work_term_reports(student_id, file_name, file_path, file_size)
                VALUES (?, ?, ?, ?)
                """,
                (g.user["id"], safe_name, str(save_path.relative_to(BASE_DIR)), size),
            )

            execute(
                "UPDATE student_profiles SET work_term_completed = 1 WHERE user_id = ?",
                (g.user["id"],),
            )
            flash("Work-term report uploaded.")
            return redirect(url_for("student_work_term"))

        reports = query_all(
            """
            SELECT *
            FROM work_term_reports
            WHERE student_id = ?
            ORDER BY uploaded_at DESC
            """,
            (g.user["id"],),
        )
        return render_template(
            "student_work_term.html",
            reports=reports,
            deadline=WORK_TERM_DEADLINE.isoformat(),
            uploads_open=date.today() <= WORK_TERM_DEADLINE,
        )

    @app.route("/work-term/template")
    @role_required("student")
    def work_term_template():
        return send_from_directory(
            BASE_DIR / "static",
            "work_term_template.txt",
            as_attachment=True,
            download_name="work_term_report_template.txt",
        )

    @app.route("/recruiter/jobs", methods=["GET", "POST"])
    @role_required("recruiter")
    def recruiter_jobs():
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip()
            requirements = request.form.get("requirements", "").strip()
            deadline = request.form.get("deadline", "").strip()

            if not title or not description or not requirements or not deadline:
                flash("All job fields are required.")
                return redirect(url_for("recruiter_jobs"))

            cursor = execute(
                """
                INSERT INTO jobs(recruiter_id, title, description, requirements, deadline)
                VALUES (?, ?, ?, ?, ?)
                """,
                (g.user["id"], title, description, requirements, deadline),
            )
            job_id = cursor.lastrowid

            students = query_all("SELECT id FROM users WHERE role = 'student'")
            for student in students:
                create_daily_notification_once(
                    student["id"],
                    f"New job posted: {title}",
                    "job",
                )

            flash("Job posting created.")
            return redirect(url_for("recruiter_jobs"))

        jobs = query_all(
            "SELECT * FROM jobs WHERE recruiter_id = ? ORDER BY created_at DESC", (g.user["id"],)
        )
        analytics = query_all(
            """
            SELECT jobs.id, jobs.title,
                   COUNT(applications.id) AS total_apps,
                   SUM(CASE WHEN applications.status = 'accepted' THEN 1 ELSE 0 END) AS accepted_apps
            FROM jobs
            LEFT JOIN applications ON applications.job_id = jobs.id
            WHERE jobs.recruiter_id = ?
            GROUP BY jobs.id, jobs.title
            ORDER BY jobs.created_at DESC
            """,
            (g.user["id"],),
        )
        return render_template("recruiter_jobs.html", jobs=jobs, analytics=analytics)

    @app.route("/recruiter/jobs/<int:job_id>/update", methods=["POST"])
    @role_required("recruiter")
    def update_job(job_id: int):
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        requirements = request.form.get("requirements", "").strip()
        deadline = request.form.get("deadline", "").strip()

        execute(
            """
            UPDATE jobs
            SET title = ?, description = ?, requirements = ?, deadline = ?
            WHERE id = ? AND recruiter_id = ?
            """,
            (title, description, requirements, deadline, job_id, g.user["id"]),
        )
        flash("Job posting updated.")
        return redirect(url_for("recruiter_jobs"))

    @app.route("/recruiter/jobs/<int:job_id>/delete", methods=["POST"])
    @role_required("recruiter")
    def delete_job(job_id: int):
        execute("DELETE FROM jobs WHERE id = ? AND recruiter_id = ?", (job_id, g.user["id"]))
        flash("Job posting deleted.")
        return redirect(url_for("recruiter_jobs"))

    @app.route("/recruiter/applications")
    @role_required("recruiter")
    def recruiter_applications():
        apps = query_all(
            """
            SELECT applications.id, applications.status, applications.applied_at,
                   applications.cover_letter, applications.transcript_notes,
                   users.username AS student_name, users.id AS student_id,
                   jobs.title AS job_title,
                   CASE WHEN shortlists.id IS NULL THEN 0 ELSE 1 END AS shortlisted
            FROM applications
            JOIN jobs ON jobs.id = applications.job_id
            JOIN users ON users.id = applications.student_id
            LEFT JOIN shortlists
                ON shortlists.application_id = applications.id
               AND shortlists.recruiter_id = jobs.recruiter_id
            WHERE jobs.recruiter_id = ?
            ORDER BY applications.applied_at DESC
            """,
            (g.user["id"],),
        )
        return render_template("recruiter_applications.html", applications=apps)

    @app.route("/recruiter/applications/<int:application_id>/status", methods=["POST"])
    @role_required("recruiter")
    def update_application_status(application_id: int):
        status = request.form.get("status", "pending")
        if status not in {"pending", "accepted", "rejected"}:
            flash("Invalid status.")
            return redirect(url_for("recruiter_applications"))

        app_row = query_one(
            """
            SELECT applications.student_id, jobs.title
            FROM applications
            JOIN jobs ON jobs.id = applications.job_id
            WHERE applications.id = ?
              AND jobs.recruiter_id = ?
            """,
            (application_id, g.user["id"]),
        )

        execute(
            """
            UPDATE applications
            SET status = ?
            WHERE id = ?
              AND job_id IN (SELECT id FROM jobs WHERE recruiter_id = ?)
            """,
            (status, application_id, g.user["id"]),
        )

        if app_row:
            create_notification(
                app_row["student_id"],
                f"Application update: {app_row['title']} is now {status}.",
                "application",
                url_for("student_applications"),
            )

        flash("Application status updated.")
        return redirect(url_for("recruiter_applications"))

    @app.route("/recruiter/applications/<int:application_id>/shortlist", methods=["POST"])
    @role_required("recruiter")
    def toggle_shortlist(application_id: int):
        exists = query_one(
            "SELECT id FROM shortlists WHERE recruiter_id = ? AND application_id = ?",
            (g.user["id"], application_id),
        )
        if exists:
            execute(
                "DELETE FROM shortlists WHERE recruiter_id = ? AND application_id = ?",
                (g.user["id"], application_id),
            )
            flash("Candidate removed from shortlist.")
        else:
            execute(
                "INSERT INTO shortlists(recruiter_id, application_id) VALUES (?, ?)",
                (g.user["id"], application_id),
            )
            flash("Candidate shortlisted.")
        return redirect(url_for("recruiter_applications"))

    @app.route("/coordinator/tracking")
    @role_required("coordinator")
    def coordinator_tracking():
        status_filter = request.args.get("status", "all")

        status_clause = ""
        params: list = []
        if status_filter in {"accepted", "rejected", "pending", "withdrawn"}:
            status_clause = "WHERE app_summary.latest_status = ?"
            params.append(status_filter)

        rows = query_all(
            f"""
            SELECT users.id AS student_id,
                   users.username,
                   users.email,
                   profiles.program,
                   COALESCE(report_summary.report_count, 0) AS report_count,
                   app_summary.latest_status
            FROM users
            LEFT JOIN student_profiles AS profiles ON profiles.user_id = users.id
            LEFT JOIN (
                SELECT student_id, COUNT(*) AS report_count
                FROM work_term_reports
                GROUP BY student_id
            ) AS report_summary ON report_summary.student_id = users.id
            LEFT JOIN (
                SELECT a.student_id, a.status AS latest_status
                FROM applications a
                JOIN (
                    SELECT student_id, MAX(applied_at) AS latest_applied_at
                    FROM applications
                    GROUP BY student_id
                ) latest
                ON latest.student_id = a.student_id
               AND latest.latest_applied_at = a.applied_at
            ) AS app_summary ON app_summary.student_id = users.id
            WHERE users.role = 'student'
            """
        )

        if status_clause:
            rows = [row for row in rows if row["latest_status"] == status_filter]

        missing_reports = [row for row in rows if row["report_count"] == 0]

        return render_template(
            "coordinator_tracking.html",
            students=rows,
            missing_reports=missing_reports,
            status_filter=status_filter,
            deadline=WORK_TERM_DEADLINE.isoformat(),
        )

    @app.route("/coordinator/reminder/<int:student_id>", methods=["POST"])
    @role_required("coordinator")
    def send_reminder(student_id: int):
        student = query_one("SELECT id, username FROM users WHERE id = ? AND role = 'student'", (student_id,))
        if student is None:
            flash("Student not found.")
            return redirect(url_for("coordinator_tracking"))

        execute(
            "INSERT INTO messages(sender_id, receiver_id, body) VALUES (?, ?, ?)",
            (
                g.user["id"],
                student_id,
                "Reminder: Please submit your missing work-term documentation.",
            ),
        )
        create_notification(
            student_id,
            "Coordinator reminder: You have missing documents to submit.",
            "reminder",
            url_for("student_work_term"),
        )
        flash(f"Reminder sent to {student['username']}.")
        return redirect(url_for("coordinator_tracking"))

    @app.route("/messages", methods=["GET", "POST"])
    @role_required("student", "coordinator")
    def messages():
        if g.user["role"] == "student":
            recipients = query_all("SELECT id, username FROM users WHERE role = 'coordinator' ORDER BY username")
        else:
            recipients = query_all("SELECT id, username FROM users WHERE role = 'student' ORDER BY username")

        if request.method == "POST":
            receiver_id = request.form.get("receiver_id", type=int)
            body = request.form.get("body", "").strip()
            valid_ids = {row["id"] for row in recipients}

            if receiver_id not in valid_ids or not body:
                flash("Choose a valid recipient and message text.")
                return redirect(url_for("messages"))

            execute(
                "INSERT INTO messages(sender_id, receiver_id, body) VALUES (?, ?, ?)",
                (g.user["id"], receiver_id, body),
            )
            create_notification(receiver_id, f"New message from {g.user['username']}", "message", url_for("messages"))
            flash("Message sent.")
            return redirect(url_for("messages"))

        thread = query_all(
            """
            SELECT messages.*, sender.username AS sender_name, receiver.username AS receiver_name
            FROM messages
            JOIN users AS sender ON sender.id = messages.sender_id
            JOIN users AS receiver ON receiver.id = messages.receiver_id
            WHERE messages.sender_id = ? OR messages.receiver_id = ?
            ORDER BY messages.sent_at DESC
            """,
            (g.user["id"], g.user["id"]),
        )
        return render_template("messages.html", recipients=recipients, thread=thread)

    @app.route("/notifications")
    @login_required
    def notifications():
        rows = query_all(
            """
            SELECT *
            FROM notifications
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (g.user["id"],),
        )
        return render_template("notifications.html", notifications=rows)

    @app.route("/notifications/poll")
    @login_required
    def poll_notifications():
        unread_count = query_one(
            "SELECT COUNT(*) AS total FROM notifications WHERE user_id = ? AND is_read = 0",
            (g.user["id"],),
        )
        latest = query_all(
            """
            SELECT id, message, kind, created_at, link
            FROM notifications
            WHERE user_id = ? AND is_read = 0
            ORDER BY created_at DESC
            LIMIT 5
            """,
            (g.user["id"],),
        )
        return jsonify(
            {
                "unread_count": unread_count["total"] if unread_count else 0,
                "latest": [dict(row) for row in latest],
            }
        )

    @app.route("/notifications/<int:notification_id>/read", methods=["POST"])
    @login_required
    def mark_notification_read(notification_id: int):
        execute(
            "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
            (notification_id, g.user["id"]),
        )
        return redirect(url_for("notifications"))

    with app.app_context():
        init_db()

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
