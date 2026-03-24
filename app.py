import sqlite3
from datetime import date
from functools import wraps
from pathlib import Path

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "coop_support.db"


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "sprint1-group43-dev-secret"
    app.config["DATABASE"] = str(DB_PATH)

    @app.before_request
    def load_logged_in_user() -> None:
        user_id = session.get("user_id")
        g.user = None
        if user_id is not None:
            g.user = query_one("SELECT * FROM users WHERE id = ?", (user_id,))

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
                execute(
                    "INSERT INTO users(username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    (username, email, generate_password_hash(password), role),
                )
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

            session.clear()
            session["user_id"] = user["id"]
            flash("Logged in successfully.")
            return redirect(url_for("dashboard"))

        return render_template("login.html")

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
            return render_template("student_dashboard.html", profile=profile)
        if g.user["role"] == "recruiter":
            job_count = query_one(
                "SELECT COUNT(*) AS total FROM jobs WHERE recruiter_id = ?", (g.user["id"],)
            )
            return render_template("recruiter_dashboard.html", job_count=job_count["total"])

        student_count = query_one("SELECT COUNT(*) AS total FROM users WHERE role = 'student'")
        return render_template("coordinator_dashboard.html", student_count=student_count["total"])

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
            flash("Profile saved.")
            return redirect(url_for("student_profile"))

        profile = query_one("SELECT * FROM student_profiles WHERE user_id = ?", (g.user["id"],))
        return render_template("student_profile.html", profile=profile)

    @app.route("/student/jobs")
    @role_required("student")
    def student_jobs():
        q = request.args.get("q", "").strip().lower()
        today = str(date.today())

        if q:
            jobs = query_all(
                """
                SELECT jobs.*, users.username AS recruiter_name
                FROM jobs
                JOIN users ON users.id = jobs.recruiter_id
                WHERE jobs.deadline >= ?
                  AND (LOWER(jobs.title) LIKE ? OR LOWER(jobs.description) LIKE ? OR LOWER(jobs.requirements) LIKE ?)
                ORDER BY jobs.deadline ASC
                """,
                (today, f"%{q}%", f"%{q}%", f"%{q}%"),
            )
        else:
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

        return render_template("student_jobs.html", jobs=jobs, q=q)

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

            execute(
                """
                INSERT INTO jobs(recruiter_id, title, description, requirements, deadline)
                VALUES (?, ?, ?, ?, ?)
                """,
                (g.user["id"], title, description, requirements, deadline),
            )
            flash("Job posting created.")
            return redirect(url_for("recruiter_jobs"))

        jobs = query_all(
            "SELECT * FROM jobs WHERE recruiter_id = ? ORDER BY created_at DESC", (g.user["id"],)
        )
        return render_template("recruiter_jobs.html", jobs=jobs)

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
                   users.username AS student_name, jobs.title AS job_title
            FROM applications
            JOIN jobs ON jobs.id = applications.job_id
            JOIN users ON users.id = applications.student_id
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

        execute(
            """
            UPDATE applications
            SET status = ?
            WHERE id = ?
              AND job_id IN (SELECT id FROM jobs WHERE recruiter_id = ?)
            """,
            (status, application_id, g.user["id"]),
        )
        flash("Application status updated.")
        return redirect(url_for("recruiter_applications"))

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

    with app.app_context():
        init_db()

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
