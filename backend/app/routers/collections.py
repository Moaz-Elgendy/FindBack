"""Collections reference only memories owned by the authenticated user."""
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session
from app.auth import get_current_user
from app.database import get_db
from app.models import Collection, CollectionItem, Item
from app.utils.canonical import canonical_url

router = APIRouter(prefix='/api/v1/collections', tags=['collections'])


class CollectionWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    urls: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator('name')
    @classmethod
    def name_not_blank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Collection name cannot be blank')
        return value

    @field_validator('urls')
    @classmethod
    def normalized_urls(cls, values):
        if any(len(value) > 4096 for value in values):
            raise ValueError('URL too long')
        return list(dict.fromkeys(canonical_url(value) for value in values))


def serialize(db, collection):
    urls = (db.query(Item.canonical_url).join(CollectionItem, CollectionItem.item_id == Item.id)
            .filter(CollectionItem.collection_id == collection.id, CollectionItem.user_id == collection.user_id,
                    Item.user_id == collection.user_id).order_by(Item.canonical_url).all())
    return {'id': str(collection.id), 'name': collection.name, 'urls': [row[0] for row in urls],
            'created_at': collection.created_at, 'updated_at': collection.updated_at}


@router.get('')
def list_collections(db: Session = Depends(get_db), user=Depends(get_current_user)):
    rows = db.query(Collection).filter(Collection.user_id == user.id).order_by(Collection.created_at).all()
    return {'collections': [serialize(db, row) for row in rows]}


@router.put('/{collection_id}')
def save_collection(collection_id: UUID, body: CollectionWrite, db: Session = Depends(get_db), user=Depends(get_current_user)):
    collection = db.get(Collection, collection_id)
    if collection is not None and collection.user_id != user.id:
        raise HTTPException(404, 'not found')
    items = db.query(Item.id).filter(Item.user_id == user.id, Item.canonical_url.in_(body.urls)).all() if body.urls else []
    if len(items) != len(body.urls):
        raise HTTPException(404, 'Save these memories before adding them to a collection')
    if collection is None:
        collection = Collection(id=collection_id, user_id=user.id, name=body.name)
        db.add(collection)
        db.flush()
    collection.name = body.name
    collection.updated_at = datetime.now(timezone.utc)
    db.query(CollectionItem).filter(CollectionItem.collection_id == collection_id, CollectionItem.user_id == user.id).delete()
    db.add_all(CollectionItem(collection_id=collection_id, item_id=row.id, user_id=user.id) for row in items)
    db.commit()
    return serialize(db, collection)


@router.delete('/{collection_id}', status_code=204)
def delete_collection(collection_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    db.query(Collection).filter(Collection.id == collection_id, Collection.user_id == user.id).delete()
    db.commit()
