from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from datetime import datetime
import os
import requests
import pytz
import logging
import traceback
from sqlalchemy.exc import SQLAlchemyError

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///helpdesk.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your_secret_key_here'  # Add this line
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='Open')
    priority = db.Column(db.String(20), default='Medium')
    category = db.Column(db.String(50))
    assigned_to = db.Column(db.String(100))
    requester_name = db.Column(db.String(100), nullable=False)
    requester_email = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted = db.Column(db.Boolean, default=False)  # New column for soft delete
    jira_issue_key = db.Column(db.String(20))  # New column for JIRA issue key

    def to_dict(self):
        # Assume UTC timezone for stored dates
        utc = pytz.UTC
        # Convert to US/Pacific timezone (you can change this to any desired timezone)
        pacific = pytz.timezone('US/Pacific')
        created_at_pacific = utc.localize(self.created_at).astimezone(pacific)
        updated_at_pacific = utc.localize(self.updated_at).astimezone(pacific)
        
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'status': self.status,
            'priority': self.priority,
            'category': self.category,
            'assigned_to': self.assigned_to,
            'requester_name': self.requester_name,
            'requester_email': self.requester_email,
            'created_at': created_at_pacific.strftime('%m/%d/%Y %I:%M %p'),
            'updated_at': updated_at_pacific.strftime('%m/%d/%Y %I:%M %p'),
            'created_at_iso': self.created_at.isoformat(),
            'updated_at_iso': self.updated_at.isoformat(),
            'jira_issue_key': self.jira_issue_key,
            'jira_issue_url': self.get_jira_issue_url()
        }

    def get_jira_issue_url(self):
        jira_settings = get_jira_settings()
        if jira_settings and self.jira_issue_key:
            return f"{jira_settings['server']}/browse/{self.jira_issue_key}"
        return None
class TicketNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=False)
    note_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
class IntegrationSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    integration_name = db.Column(db.String(50), nullable=False, unique=True)
    enabled = db.Column(db.Boolean, default=False)
    webhook_url = db.Column(db.String(200))
    api_url = db.Column(db.String(200))
    username = db.Column(db.String(100))
    api_token = db.Column(db.String(100))
    project_key = db.Column(db.String(20))

def get_slack_webhook_url():
    slack_setting = IntegrationSetting.query.filter_by(integration_name='Slack').first()
    return slack_setting.webhook_url if slack_setting and slack_setting.enabled else None

def get_jira_settings():
    jira_setting = IntegrationSetting.query.filter_by(integration_name='JIRA').first()
    if jira_setting and jira_setting.enabled:
        return {
            'server': jira_setting.api_url,
            'username': jira_setting.username,
            'api_token': jira_setting.api_token,
            'project_key': jira_setting.project_key
        }
    return None

def send_slack_notification(ticket):
    with app.app_context():
        slack_webhook_url = get_slack_webhook_url()
        if not slack_webhook_url:
            logger.warning("Slack integration is not enabled or webhook URL is not set.")
            return

        ticket_url = url_for('edit_ticket', id=ticket.id, _external=True)
        message = f"""
New Ticket Created:
*<{ticket_url}|#{ticket.id}: {ticket.title}>*
*Description:* {ticket.description}
*Priority:* {ticket.priority}
*Category:* {ticket.category}
*Requester:* {ticket.requester_name} ({ticket.requester_email})
        """
        payload = {
            'text': 'New Ticket Created',
            'attachments': [
                {
                    'color': '#36a64f',
                    'text': message,
                    'actions': [
                        {
                            'type': 'button',
                            'text': 'View Ticket',
                            'url': ticket_url
                        }
                    ]
                }
            ]
        }
        try:
            response = requests.post(slack_webhook_url, json=payload)
            response.raise_for_status()
            logger.info(f"Slack notification sent for ticket #{ticket.id}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending Slack notification for ticket #{ticket.id}: {e}")

def create_jira_issue(ticket):
    jira_settings = get_jira_settings()
    if not jira_settings:
        logger.warning("JIRA integration is not enabled or settings are not configured.")
        return None

    try:
        logger.debug(f"Connecting to JIRA server: {jira_settings['server']}")
        jira = JIRA(server=jira_settings['server'],
                    basic_auth=(jira_settings['username'], jira_settings['api_token']))

        issue_dict = {
            'project': {'key': jira_settings['project_key']},
            'summary': ticket.title,
            'description': ticket.description,
            'issuetype': {'name': 'Task'},
            # 'priority': {'name': ticket.priority},
        }

        logger.debug(f"Creating JIRA issue with data: {issue_dict}")
        new_issue = jira.create_issue(fields=issue_dict)
        logger.info(f"JIRA issue created: {new_issue.key} for ticket #{ticket.id}")
        return new_issue.key
    except Exception as e:
        logger.error(f"Error creating JIRA issue for ticket #{ticket.id}: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return None

@app.route('/')
@app.route('/tickets')
def tickets():
    tickets = Ticket.query.filter((Ticket.deleted == False) | (Ticket.deleted == None)).order_by(Ticket.created_at.desc()).all()
    return render_template('tickets.html', tickets=[ticket.to_dict() for ticket in tickets])

@app.route('/tickets/new', methods=['GET', 'POST'])
def new_ticket():
    if request.method == 'POST':
        try:
            # Create new ticket
            new_ticket = Ticket(
                title=request.form['title'],
                description=request.form['description'],
                status=request.form['status'],
                priority=request.form['priority'],
                category=request.form['category'],
                assigned_to=request.form['assigned_to'],
                requester_name=request.form['requester_name'],
                requester_email=request.form['requester_email']
            )
            db.session.add(new_ticket)
            db.session.commit()
            logger.info(f"Ticket committed to database: #{new_ticket.id}")

            # Send Slack notification
            send_slack_notification(new_ticket)

            # Create JIRA issue and store the key
            jira_issue_key = create_jira_issue(new_ticket)
            if jira_issue_key:
                logger.debug(f"JIRA issue key received: {jira_issue_key}")
                new_ticket.jira_issue_key = jira_issue_key
                db.session.commit()
                logger.info(f"JIRA issue key saved for ticket #{new_ticket.id}")
            else:
                logger.warning(f"No JIRA issue key returned for ticket #{new_ticket.id}")

            # Final verification
            saved_ticket = Ticket.query.get(new_ticket.id)
            if saved_ticket is None:
                raise Exception(f"Failed to retrieve ticket #{new_ticket.id} from database after commit")

            logger.info(f"Final verification: Ticket #{saved_ticket.id}, JIRA key: {saved_ticket.jira_issue_key}")

            if saved_ticket.jira_issue_key != jira_issue_key:
                logger.error(f"JIRA issue key mismatch: Expected {jira_issue_key}, got {saved_ticket.jira_issue_key}")
                raise Exception("JIRA issue key not saved correctly")

            flash('Ticket created successfully.', 'success')
            return redirect(url_for('tickets'))

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error creating ticket: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            flash('An error occurred while creating the ticket. Please try again.', 'error')
        except Exception as e:
            db.session.rollback()
            logger.error(f"Unexpected error creating ticket: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            flash('An unexpected error occurred. Please try again.', 'error')

    return render_template('new_ticket.html')

@app.route('/tickets/<int:id>/edit', methods=['GET', 'POST'])
def edit_ticket(id):
    ticket = Ticket.query.get_or_404(id)
    if request.method == 'POST':
        ticket.title = request.form['title']
        ticket.description = request.form['description']
        ticket.status = request.form['status']
        ticket.priority = request.form['priority']
        ticket.category = request.form['category']
        ticket.assigned_to = request.form['assigned_to']
        ticket.requester_name = request.form['requester_name']
        ticket.requester_email = request.form['requester_email']
        db.session.commit()
        flash('Ticket updated successfully.', 'success')
        return redirect(url_for('tickets'))
    notes = TicketNote.query.filter_by(ticket_id=id).order_by(TicketNote.created_at.desc()).all()
    return render_template('edit_ticket.html', ticket=ticket, notes=notes)

@app.route('/tickets/<int:id>/notes', methods=['POST'])
def add_note(id):
    Ticket.query.get_or_404(id)
    note_text = request.form.get('note_text', '').strip()
    if note_text:
        note = TicketNote(ticket_id=id, note_text=note_text)
        db.session.add(note)
        db.session.commit()
        flash('Note added.', 'success')
    return redirect(url_for('edit_ticket', id=id))

@app.route('/tickets/<int:id>/delete', methods=['POST'])
def delete_ticket(id):
    ticket = Ticket.query.get_or_404(id)
    ticket.deleted = True
    db.session.commit()
    flash('Ticket deleted successfully.', 'success')
    return redirect(url_for('tickets'))

@app.route('/integrations')
def integrations():
    return render_template('integrations.html')

@app.route('/integrations/slack', methods=['GET', 'POST'])
def slack_integration():
    slack_setting = IntegrationSetting.query.filter_by(integration_name='Slack').first()
    if not slack_setting:
        slack_setting = IntegrationSetting(integration_name='Slack')
        db.session.add(slack_setting)
        db.session.commit()

    if request.method == 'POST':
        slack_setting.enabled = 'enabled' in request.form
        slack_setting.webhook_url = request.form['webhook_url']
        db.session.commit()
        flash('Slack integration settings have been saved successfully.', 'success')
        return redirect(url_for('integrations'))

    return render_template('slack_integration.html', slack_setting=slack_setting)

@app.route('/integrations/jira', methods=['GET', 'POST'])
def jira_integration():
    jira_setting = IntegrationSetting.query.filter_by(integration_name='JIRA').first()
    if not jira_setting:
        jira_setting = IntegrationSetting(integration_name='JIRA')
        db.session.add(jira_setting)
        db.session.commit()

    if request.method == 'POST':
        jira_setting.enabled = 'enabled' in request.form
        jira_setting.api_url = request.form['api_url']
        jira_setting.username = request.form['username']
        jira_setting.api_token = request.form['api_token']
        jira_setting.project_key = request.form['project_key']
        db.session.commit()
        flash('JIRA integration settings have been saved successfully.', 'success')
        return redirect(url_for('integrations'))

    return render_template('jira_integration.html', jira_setting=jira_setting)

@app.route('/integrations/salesforce')
def salesforce_integration():
    return render_template('salesforce_integration.html')

@app.route('/integrations/webhook')
def webhook_integration():
    return render_template('webhook_integration.html')

@app.route('/workflows')
def workflows():
    return render_template('workflows.html')

@app.route('/team')
def team():
    return render_template('team.html')

@app.route('/settings')
def settings():
    return render_template('settings.html')

# New routes for knowledge base and support ticket submission
@app.route('/knowledge-base')
def knowledge_base():
    return render_template('knowledge_base.html')

@app.route('/submit-ticket', methods=['GET', 'POST'])
def submit_ticket():
    if request.method == 'POST':
        try:
            new_ticket = Ticket(
                title=request.form['subject'],
                description=request.form['description'],
                requester_name=request.form['name'],
                requester_email=request.form['email'],
                status='Open',
                priority='Medium',
                category='Support'
            )
            db.session.add(new_ticket)
            db.session.commit()
            
            # Send Slack notification
            send_slack_notification(new_ticket)

            # Create JIRA issue
            jira_issue_key = create_jira_issue(new_ticket)
            if jira_issue_key:
                new_ticket.jira_issue_key = jira_issue_key
                db.session.commit()

            flash('Your support ticket has been submitted successfully.', 'success')
            return redirect(url_for('knowledge_base'))
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error submitting support ticket: {str(e)}")
            flash('An error occurred while submitting your ticket. Please try again.', 'error')
    
    return render_template('submit_ticket.html')
@app.route('/dashboard')
def dashboard():
    total = Ticket.query.filter((Ticket.deleted == False) | (Ticket.deleted == None)).count()
    open_count = Ticket.query.filter_by(status='Open', deleted=False).count()
    inprogress_count = Ticket.query.filter_by(status='In Progress', deleted=False).count()
    closed_count = Ticket.query.filter_by(status='Closed', deleted=False).count()
    recent = Ticket.query.filter((Ticket.deleted == False) | (Ticket.deleted == None)).order_by(Ticket.created_at.desc()).limit(5).all()
    return render_template('workflows.html',
        total=total, open_count=open_count,
        inprogress_count=inprogress_count, closed_count=closed_count,
        recent=[t.to_dict() for t in recent])

@app.route('/create-ticket', methods=['GET', 'POST'])
def create_ticket():
    return render_template('submit_ticket.html')

@app.route('/analytics')
def analytics():
    from collections import defaultdict
    from datetime import timedelta
    open_count = Ticket.query.filter_by(status='Open', deleted=False).count()
    inprogress_count = Ticket.query.filter_by(status='In Progress', deleted=False).count()
    closed_count = Ticket.query.filter_by(status='Closed', deleted=False).count()
    status_data = {
        'labels': ['Open', 'In Progress', 'Closed'],
        'values': [open_count, inprogress_count, closed_count]
    }
    today = datetime.utcnow().date()
    daily_counts = defaultdict(int)
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        daily_counts[day.strftime('%b %d')] = 0
    tickets = Ticket.query.filter((Ticket.deleted == False) | (Ticket.deleted == None)).all()
    for t in tickets:
        day_str = t.created_at.strftime('%b %d')
        if day_str in daily_counts:
            daily_counts[day_str] += 1
    daily_data = {'labels': list(daily_counts.keys()), 'values': list(daily_counts.values())}
    return render_template('integrations.html', status_data=status_data, daily_data=daily_data)
# REST API endpoints
@app.route('/api/tickets', methods=['POST'])
def api_create_ticket():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    required = ['customer_name', 'customer_email', 'subject', 'description']
    for field in required:
        if field not in data:
            return jsonify({'error': f'Missing field: {field}'}), 400
    ticket = Ticket(
        requester_name=data['customer_name'],
        requester_email=data['customer_email'],
        title=data['subject'],
        description=data['description'],
        status='Open',
        priority='Medium'
    )
    db.session.add(ticket)
    db.session.commit()
    ticket_id = f"TKT-{ticket.id:03d}"
    return jsonify({'ticket_id': ticket_id, 'created_at': ticket.created_at.isoformat()}), 201

@app.route('/api/tickets', methods=['GET'])
def api_list_tickets():
    query = Ticket.query.filter((Ticket.deleted == False) | (Ticket.deleted == None))
    status = request.args.get('status')
    search = request.args.get('search')
    if status:
        query = query.filter_by(status=status)
    if search:
        s = f'%{search}%'
        query = query.filter(
            db.or_(Ticket.requester_name.ilike(s), Ticket.requester_email.ilike(s),
                   Ticket.title.ilike(s), Ticket.description.ilike(s))
        )
    tickets = query.order_by(Ticket.created_at.desc()).all()
    return jsonify([{
        'ticket_id': f"TKT-{t.id:03d}",
        'customer_name': t.requester_name,
        'subject': t.title,
        'status': t.status,
        'created_at': t.created_at.isoformat()
    } for t in tickets])

@app.route('/api/tickets/<int:id>', methods=['GET'])
def api_get_ticket(id):
    t = Ticket.query.get_or_404(id)
    notes = TicketNote.query.filter_by(ticket_id=id).order_by(TicketNote.created_at).all()
    return jsonify({
        'ticket_id': f"TKT-{t.id:03d}",
        'customer_name': t.requester_name,
        'customer_email': t.requester_email,
        'subject': t.title,
        'description': t.description,
        'status': t.status,
        'notes': [{'note_text': n.note_text, 'created_at': n.created_at.isoformat()} for n in notes]
    })

@app.route('/api/tickets/<int:id>', methods=['PUT'])
def api_update_ticket(id):
    t = Ticket.query.get_or_404(id)
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    if 'status' in data:
        t.status = data['status']
    if 'notes' in data and data['notes']:
        note = TicketNote(ticket_id=id, note_text=data['notes'])
        db.session.add(note)
    t.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'updated_at': t.updated_at.isoformat()})
if __name__ == '__main__':
with app.app_context():
    db.create_all()

app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))