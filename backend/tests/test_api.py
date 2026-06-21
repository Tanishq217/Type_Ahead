import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db

from app.main import app
from app.models import SearchQuery
from app.cache import cache_manager

# Setup SQLite test database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Database Override Dependency
def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    # Add dummy data
    db.add_all([
        SearchQuery(query_text="iphone", total_count=1000),
        SearchQuery(query_text="iphone 15", total_count=500),
        SearchQuery(query_text="iphone charger", total_count=200),
        SearchQuery(query_text="java tutorial", total_count=150),
    ])
    db.commit()
    db.close()
    yield
    Base.metadata.drop_all(bind=engine)

def test_suggest_empty_prefix():
    response = client.get("/suggest?q=")
    assert response.status_code == 200
    assert response.json()["suggestions"] == []
    assert response.json()["source"] == "empty_input"

def test_suggest_prefix_matching():
    response = client.get("/suggest?q=ip")
    assert response.status_code == 200
    data = response.json()
    suggestions = data["suggestions"]
    
    assert len(suggestions) == 3
    assert suggestions[0] == "iphone"
    assert suggestions[1] == "iphone 15"
    assert suggestions[2] == "iphone charger"

def test_suggest_case_insensitive():
    response = client.get("/suggest?q=iPhOnE")
    assert response.status_code == 200
    data = response.json()
    assert "iphone" in data["suggestions"]

def test_search_submission():
    # Submit search query
    response = client.post("/search", json={"query": "python tutorial"})
    assert response.status_code == 200
    assert response.json() == {"message": "Searched"}

def test_cache_debug_endpoint():
    # Check debug endpoint routing
    response = client.get("/cache/debug?prefix=ip")
    assert response.status_code == 200
    data = response.json()
    assert data["prefix"] == "ip"
    assert "routed_node" in data
    assert "circuit_state" in data
    assert "cache_status" in data
