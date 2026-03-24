# Co-op Support App - Sprint 1 (Group 43)

This project is a Sprint 1 MVP implementation based on `Sprint_1_group_43.pdf`.

## Implemented Sprint 1 Features

1. User Registration and Login
- Student and recruiter self-registration
- Secure password hashing
- Role-based access control

2. Job Posting
- Recruiters can create, edit, and delete postings
- Students can browse and search postings

3. Student Profile
- Students can create and update profile information
- Includes school, degree, program, grades, and resume notes

4. Application Tracking
- Students can apply to jobs and view status (pending, accepted, rejected, withdrawn)
- Recruiters can review applicants and update statuses

5. Communication (Coordinator and Students)
- In-app messaging between students and coordinators
- Default coordinator account is seeded automatically

## Tech Stack

- Python 3.12
- Flask
- SQLite

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Run app:

```bash
python app.py
```

3. Open browser:

- http://127.0.0.1:5000

## Default Coordinator Account

- Username: `coord1`
- Password: `coord123`

Use this account to test coordinator-to-student messaging.

## Notes

- Database file is created automatically as `coop_support.db`.
- This sprint implementation prioritizes core functionality listed in the Sprint 1 document.
