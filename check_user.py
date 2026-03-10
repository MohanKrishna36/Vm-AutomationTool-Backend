#!/usr/bin/env python3
"""
Check current user and their reports
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.database import SessionLocal
from app.db import models

def check_user_data():
    """Check what users exist and their reports"""
    print("=== CHECKING USER DATA ===\n")
    
    db = SessionLocal()
    
    try:
        # Check all users
        users = db.query(models.User).all()
        print(f"📋 All users in database:")
        for user in users:
            print(f"   - ID: {user.id}, Username: {user.username}")
        
        # Check all reports
        reports = db.query(models.SessionReport).all()
        print(f"\n📊 All reports in database:")
        for report in reports:
            user = db.query(models.User).filter(models.User.id == report.user_id).first()
            username = user.username if user else "Unknown"
            print(f"   - Report ID: {report.id}, User ID: {report.user_id}, Username: {username}, Session: {report.session_id}")
        
        # Check if there are any reports at all
        print(f"\n📈 Total reports: {len(reports)}")
        print(f"👥 Total users: {len(users)}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()
    
    print("\n=== CHECK COMPLETE ===")

if __name__ == "__main__":
    check_user_data()
