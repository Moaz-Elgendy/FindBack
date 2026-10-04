from app.models import Chunk, Item


def test_table_args_are_on_correct_models():
    assert any(getattr(c, "name", None) == "items_user_canonical_uq" for c in Item.__table__.constraints)
    assert any(getattr(c, "name", None) == "chunks_item_idx_uq" for c in Chunk.__table__.constraints)
    assert "chunks_item_idx" in {index.name for index in Chunk.__table__.indexes}
