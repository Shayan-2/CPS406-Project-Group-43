# Co-op Support App - Sprint 2 (Group 43)

This project now includes Sprint 2 enhancements based on `Sprint_2_group_43.pdf`.

## Implemented Features

1. User Registration and Login
- Student and recruiter self-registration
- Secure password hashing
- Role-based access control
- Optional 2FA and password-reset flow

2. Job Posting
- Recruiters can create, edit, and delete postings
- Students can browse and search postings with improved filtering
- Recruiters can view posting analytics

3. Student Profile
- Students can create and update profile information
- Includes school, degree, program, grades, and resume notes
- Resume keyword feedback support

4. Application Tracking
- Students can apply to jobs and view status (pending, accepted, rejected, withdrawn)
- Recruiters can review applicants and update statuses
- Recruiters can shortlist candidates
- Students get visual progress tracking on applications

5. Communication (Coordinator and Students)
- In-app messaging between students and coordinators
- Default coordinator account is seeded automatically

6. Notifications and Recommendations
- In-app notifications for updates and reminders
- Personalized recommendation scoring using profile-to-job matching

7. Work-Term Documentation
- PDF-only upload with 10MB limit
- Deadline-aware submission window
- Downloadable work-term report template

8. Coordinator Tracking and Reminders
- Student tracking page for missing documentation and outcomes
- Reminder sending for missing reports

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
- This sprint implementation prioritizes functionality listed in the Sprint 2 document.
