from fastapi import APIRouter
router = APIRouter()
@router.get("/health")
def health_check():
    return {"status": "OK", 
            "message": "The API is healthy and running smoothly."}