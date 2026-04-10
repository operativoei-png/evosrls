import os
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import Flask, flash, redirect, render_template, request, send_from_directory, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash
from openpyxl import load_workbook

# --- INIZIALIZZAZIONE ---
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "login"

def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "evolve-industrial-2026")
    
    # FIX PER RENDER: Converte postgres:// in postgresql://
    db_url = os.getenv("DATABASE_URL", "sqlite:///" + os.path.join(app.instance_path, "evolve.db"))
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
        
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads")
    app.config["CERT_FOLDER"] = os.path.join(app.config["UPLOAD_FOLDER"], "attestati")

    os.makedirs(app.config["CERT_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    with app.app_context():
        db.create_all()
        # Utente Admin di sistema
        if not User.query.filter_by(username="admin").first():
            u = User(username="admin", role="admin")
            u.set_password("admin123!")
            db.session.add(u)
        # Impostazioni Aziendali
        if not AppSetting.query.first():
            db.session.add(AppSetting())
        db.session.commit()

    @app.context_processor
    def inject_branding():
        return {"app_settings": AppSetting.query.first()}

    register_routes(app)
    return app

# --- MODELLI ---

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), default="admin")
    def set_password(self, raw): self.password_hash = generate_password_hash(raw)
    def check_password(self, raw): return check_password_hash(self.password_hash, raw)

class AppSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(150), default="Evolve Impianti")
    logo_path = db.Column(db.String(255), default="")
    bolla_prefix = db.Column(db.String(20), default="BOL")

class Technician(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(50), default="")
    notes = db.Column(db.String(255), default="")
    certificates = db.relationship("Certificate", backref="technician", cascade="all, delete-orphan")
    items = db.relationship("WarehouseItem", backref="technician")
    tools = db.relationship("Tool", backref="technician")
    vans = db.relationship("Van", backref="technician")
    charges = db.relationship("Charge", backref="technician")

class Certificate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    technician_id = db.Column(db.Integer, db.ForeignKey("technician.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    description = db.Column(db.String(150))
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)

class WarehouseItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), nullable=False)
    category = db.Column(db.String(50), default="Materiale")
    description = db.Column(db.String(255), nullable=False)
    serial = db.Column(db.String(120), unique=True, index=True)
    serialized = db.Column(db.Boolean, default=False)
    quantity = db.Column(db.Integer, default=1)
    unit = db.Column(db.String(20), default="pz")
    min_stock = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default="generale") 
    assigned_to = db.Column(db.Integer, db.ForeignKey("technician.id"))
    client_default = db.Column(db.String(150))
    last_update = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Tool(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), nullable=False)
    serial = db.Column(db.String(120))
    description = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(40), default="disponibile")
    charge_value = db.Column(db.Float, default=0.0)
    assigned_to = db.Column(db.Integer, db.ForeignKey("technician.id"))

class Van(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plate = db.Column(db.String(30), unique=True, nullable=False)
    model = db.Column(db.String(120))
    status = db.Column(db.String(40), default="attivo")
    assigned_to = db.Column(db.Integer, db.ForeignKey("technician.id"))

class Charge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    description = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(50), default="aperto")
    notes = db.Column(db.String(255))
    technician_id = db.Column(db.Integer, db.ForeignKey("technician.id"))

class Transfer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bolla_no = db.Column(db.String(40), unique=True)
    transfer_type = db.Column(db.String(10)) 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    client = db.Column(db.String(150))
    job = db.Column(db
