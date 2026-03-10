# Reports Persistence Feature

## Overview
This feature implements persistent storage for VM session reports, allowing users to access their historical reports across different login sessions.

## Implementation Details

### Backend Changes

#### 1. Database Model (`app/db/models.py`)
- Added `SessionReport` model to store reports in the database
- Fields include:
  - `session_id`: Unique identifier for the session
  - `vm_id`, `user_id`: Foreign keys to VM and User tables
  - `session_name`: User-defined session name
  - `vm_host`: Host address of the VM
  - `start_time`, `end_time`: Session timestamps
  - `duration`: Session duration in milliseconds
  - Command metrics: `total_commands`, `successful_commands`, `failed_commands`
  - Performance metrics: `success_rate`, `average_execution_time`
  - `commands_data`: JSON string containing command history
  - `generated_at`, `created_at`: Timestamps

#### 2. API Endpoints (`app/api/reports.py`)
- `POST /reports/` - Create a new session report
- `GET /reports/` - Get all reports for the current user
- `GET /reports/{session_id}` - Get a specific report
- `DELETE /reports/{session_id}` - Delete a specific report
- `GET /reports/stats/summary` - Get summary statistics for user's reports

#### 3. Database Integration
- Reports are stored in SQLite database
- Automatic table creation on application startup
- User-specific report access control

### Frontend Changes

#### 1. VMContext Updates (`src/contexts/VMContext.jsx`)
- Modified `generateSessionReport()` to save reports to backend API
- Added `fetchReportsFromBackend()` function to retrieve reports
- Maintains localStorage as backup for offline functionality
- Automatic report fetching on component mount

#### 2. Reports Page Updates (`src/pages/Reports.jsx`)
- Added refresh button to manually reload reports from backend
- Enhanced UI with loading states
- Improved error handling and user feedback

## Usage

### For Users
1. **Automatic Saving**: Reports are automatically saved to the database when you disconnect from a VM session
2. **Persistent Access**: Your reports are available even after logging out and back in
3. **Refresh Function**: Use the "Refresh Reports" button to manually reload the latest reports
4. **Cross-Device Access**: Reports are tied to your user account, not the browser

### For Developers
1. **Database Setup**: Run `python init_db.py` to create the necessary database tables
2. **Testing**: Use `python test_reports.py` to verify functionality
3. **API Testing**: All endpoints are available at `http://127.0.0.1:8000/reports/`

## Data Flow

1. **Session End**: When a user disconnects from a VM, `generateSessionReport()` is called
2. **Report Creation**: Report data is formatted and sent to `/reports/` API endpoint
3. **Database Storage**: Backend validates data and stores in `session_reports` table
4. **Frontend Update**: Local state is updated with the saved report data
5. **Backup Storage**: Reports are also stored in localStorage as backup

## Error Handling

- **Backend Failures**: If the backend API is unavailable, reports fall back to localStorage
- **Network Issues**: Automatic retry logic and user-friendly error messages
- **Data Validation**: Backend validates all incoming report data before storage

## Security

- **User Isolation**: Reports are strictly separated by user_id
- **Authentication**: All report endpoints require valid JWT token
- **Authorization**: Users can only access their own reports

## Future Enhancements

- Report export functionality (PDF, CSV)
- Advanced filtering and search capabilities
- Report sharing between users
- Analytics and trend analysis
- Automated report cleanup/archiving

## Troubleshooting

### Common Issues

1. **Reports not saving**: Check backend server is running and database is accessible
2. **Missing reports after login**: Verify JWT token is valid and user authentication is working
3. **Slow loading**: Consider implementing pagination for large numbers of reports

### Debug Commands

```bash
# Initialize database
python init_db.py

# Test reports functionality
python test_reports.py

# Start backend server
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## Migration Notes

- Existing localStorage reports will be migrated to the backend on first load
- No data loss during the migration process
- Backward compatibility maintained for offline usage
