# ResolveDesk

A customer support ticketing CRM built with Flask and SQLite.

## Features
- Create support tickets with auto-generated IDs (TKT-001 format)
- List, search, and filter tickets by status
- View and update ticket details
- Add notes/comments to tickets
- REST API endpoints
- Dashboard with live stats
- Analytics charts

## Tech Stack
- Python + Flask
- SQLite + SQLAlchemy
- HTML + Bootstrap 5

## Setup Instructions

1. Clone the repo
   git clone <your-repo-url>
   cd helpdesk-claude-dev-ai-main

2. Create and activate virtual environment
   python -m venv venv
   venv\Scripts\activate

3. Install dependencies
   pip install -r requirements.txt

4. Run the app
   python app.py

5. Open browser at http://127.0.0.1:5000

## API Endpoints

POST   /api/tickets         — Create a ticket
GET    /api/tickets         — List all tickets (?status=Open&search=name)
GET    /api/tickets/<id>    — Get ticket details
PUT    /api/tickets/<id>    — Update status or add note