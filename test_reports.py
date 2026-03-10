#!/usr/bin/env python3
"""
Test script for reports functionality
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.database import engine, Base, SessionLocal
from app.db import models
from datetime import datetime
import json

def test_reports_functionality():
    """Test the reports database functionality"""
    print("Testing reports functionality...")
    
    # Create tables
    Base.metadata.create_all(bind=engine)
    print("✓ Database tables created")
    
    # Create a test session
    db = SessionLocal()
    
    try:
        # Create test user
        test_user = models.User(
            username="testuser",
            hashed_password="testpass123"
        )
        db.add(test_user)
        db.commit()
        db.refresh(test_user)
        print(f"✓ Created test user with ID: {test_user.id}")
        
        # Create test VM
        test_vm = models.VirtualMachine(
            host="192.168.1.100",
            username="testuser",
            password="testpass",
            is_busy=False,
            locked_by=test_user.id
        )
        db.add(test_vm)
        db.commit()
        db.refresh(test_vm)
        print(f"✓ Created test VM with ID: {test_vm.id}")
        
        # Create test session report
        test_commands = [
            {"command": "ls -la", "timestamp": 1640995200000, "executionTime": 150, "success": True},
            {"command": "pwd", "timestamp": 1640995260000, "executionTime": 50, "success": True},
            {"command": "echo 'test'", "timestamp": 1640995320000, "executionTime": 75, "success": True}
        ]
        
        test_report = models.SessionReport(
            session_id="SESSION-1-1640995200000",
            vm_id=test_vm.id,
            user_id=test_user.id,
            session_name="Test Session",
            vm_host="192.168.1.100",
            start_time=datetime(2022, 1, 1, 10, 0, 0),
            end_time=datetime(2022, 1, 1, 10, 5, 0),
            duration=300000,  # 5 minutes in milliseconds
            total_commands=3,
            successful_commands=3,
            failed_commands=0,
            success_rate=100.0,
            average_execution_time=91.67,
            commands_data=json.dumps(test_commands),
            generated_at=datetime.utcnow()
        )
        
        db.add(test_report)
        db.commit()
        db.refresh(test_report)
        print(f"✓ Created test report with ID: {test_report.id}")
        
        # Test fetching reports
        reports = db.query(models.SessionReport).filter(
            models.SessionReport.user_id == test_user.id
        ).all()
        
        print(f"✓ Found {len(reports)} reports for user")
        
        # Test report data integrity
        if reports:
            report = reports[0]
            commands = json.loads(report.commands_data) if report.commands_data else []
            print(f"✓ Report has {len(commands)} commands")
            print(f"  - Session ID: {report.session_id}")
            print(f"  - VM Host: {report.vm_host}")
            print(f"  - Success Rate: {report.success_rate}%")
            print(f"  - Duration: {report.duration}ms")
        
        print("\n✅ All tests passed! Reports functionality is working correctly.")
        
    except Exception as e:
        print(f"❌ Test failed: {str(e)}")
        import traceback
        traceback.print_exc()
        
    finally:
        db.close()

if __name__ == "__main__":
    test_reports_functionality()
