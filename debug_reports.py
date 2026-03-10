#!/usr/bin/env python3
"""
Debug script to check reports functionality step by step
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.database import engine, Base, SessionLocal
from app.db import models
from datetime import datetime
import json
from sqlalchemy import inspect

def debug_database():
    """Debug database setup and functionality"""
    print("=== DEBUGGING REPORTS FUNCTIONALITY ===\n")
    
    # 1. Check database connection
    try:
        db = SessionLocal()
        print("✓ Database connection successful")
        db.close()
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return
    
    # 2. Check table creation
    try:
        print("\n--- Checking Database Tables ---")
        Base.metadata.create_all(bind=engine)
        print("✓ Tables created/verified")
        
        # List all tables
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        print(f"Available tables: {tables}")
        
        if 'session_reports' not in tables:
            print("❌ session_reports table NOT found!")
            return
        else:
            print("✓ session_reports table found")
            
    except Exception as e:
        print(f"❌ Table creation failed: {e}")
        return
    
    # 3. Test model creation
    try:
        print("\n--- Testing Model Creation ---")
        db = SessionLocal()
        
        # Check if we can create a report object
        test_report = models.SessionReport(
            session_id="TEST-SESSION-123",
            vm_id=1,
            user_id=1,
            session_name="Test Session",
            vm_host="192.168.1.100",
            start_time=datetime(2024, 1, 1, 10, 0, 0),
            end_time=datetime(2024, 1, 1, 10, 5, 0),
            duration=300000,
            total_commands=5,
            successful_commands=4,
            failed_commands=1,
            success_rate=80.0,
            average_execution_time=150.5,
            commands_data=json.dumps([
                {"command": "ls", "timestamp": 1640995200000, "executionTime": 100, "success": True},
                {"command": "pwd", "timestamp": 1640995260000, "executionTime": 50, "success": True}
            ])
        )
        print("✓ Report object created successfully")
        
        # Test database save
        db.add(test_report)
        db.commit()
        db.refresh(test_report)
        print(f"✓ Report saved to database with ID: {test_report.id}")
        
        # Test database retrieval
        saved_report = db.query(models.SessionReport).filter(
            models.SessionReport.session_id == "TEST-SESSION-123"
        ).first()
        
        if saved_report:
            print(f"✓ Report retrieved successfully: {saved_report.session_id}")
            commands = json.loads(saved_report.commands_data) if saved_report.commands_data else []
            print(f"✓ Commands data parsed: {len(commands)} commands")
        else:
            print("❌ Report retrieval failed")
            
        # Clean up test data
        db.delete(test_report)
        db.commit()
        print("✓ Test data cleaned up")
        
    except Exception as e:
        print(f"❌ Model operation failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()
    
    print("\n=== DEBUG COMPLETE ===")

if __name__ == "__main__":
    debug_database()
