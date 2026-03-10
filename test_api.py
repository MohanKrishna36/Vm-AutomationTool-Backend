#!/usr/bin/env python3
"""
Test the reports API directly
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.database import engine, Base, SessionLocal
from app.db import models
from datetime import datetime
import json

def test_api():
    """Test API by creating a test report directly"""
    print("=== TESTING REPORTS API ===\n")
    
    # Create a test report directly in database
    db = SessionLocal()
    
    try:
        # First create a test user and VM if they don't exist
        user = db.query(models.User).filter(models.User.username == "testuser").first()
        if not user:
            user = models.User(
                username="testuser",
                hashed_password="testpass123"
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"✓ Created test user with ID: {user.id}")
        
        vm = db.query(models.VirtualMachine).filter(models.VirtualMachine.host == "192.168.1.100").first()
        if not vm:
            vm = models.VirtualMachine(
                host="192.168.1.100",
                username="testuser",
                password="testpass",
                is_busy=False,
                locked_by=user.id
            )
            db.add(vm)
            db.commit()
            db.refresh(vm)
            print(f"✓ Created test VM with ID: {vm.id}")
        
        # Create a test report
        test_report = models.SessionReport(
            session_id="SESSION-TEST-12345",
            vm_id=vm.id,
            user_id=user.id,
            session_name="Test VM Session",
            vm_host="192.168.1.100",
            start_time=datetime(2024, 3, 10, 10, 0, 0),
            end_time=datetime(2024, 3, 10, 10, 5, 0),
            duration=300000,  # 5 minutes
            total_commands=3,
            successful_commands=2,
            failed_commands=1,
            success_rate=66.67,
            average_execution_time=150.0,
            commands_data=json.dumps([
                {"command": "ls -la", "timestamp": 1710072000000, "executionTime": 100, "success": True},
                {"command": "pwd", "timestamp": 1710072060000, "executionTime": 50, "success": True},
                {"command": "echo 'test'", "timestamp": 1710072120000, "executionTime": 300, "success": False}
            ]),
            generated_at=datetime.utcnow()
        )
        
        db.add(test_report)
        db.commit()
        db.refresh(test_report)
        
        print(f"✅ Created test report with ID: {test_report.id}")
        print(f"   Session ID: {test_report.session_id}")
        print(f"   User ID: {test_report.user_id}")
        print(f"   VM ID: {test_report.vm_id}")
        print(f"   Commands: {len(json.loads(test_report.commands_data))}")
        
        # Now test retrieval
        reports = db.query(models.SessionReport).filter(
            models.SessionReport.user_id == user.id
        ).all()
        
        print(f"\n✅ Found {len(reports)} reports for user")
        for report in reports:
            print(f"   - {report.session_id} ({report.session_name})")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()
    
    print("\n=== API TEST COMPLETE ===")
    print("Now check the Reports page in browser - you should see this test report!")

if __name__ == "__main__":
    test_api()
