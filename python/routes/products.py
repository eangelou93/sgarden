from fastapi import APIRouter, HTTPException, status, Depends, Query
from models.product import ProductRequest, ProductResponse
from database import products_collection
from security.jwt_handler import get_current_user
from bson import ObjectId
from datetime import datetime
from typing import Optional

router = APIRouter(prefix="/api/products", tags=["products"])

# CODE QUALITY ISSUE: unused variable
service_name = "ProductService"


def product_to_response(product: dict) -> dict:
    """Convert MongoDB document to API response format."""
    return {
        "id": str(product["_id"]),
        "name": product.get("name"),
        "description": product.get("description"),
        "category": product.get("category"),
        "price": product.get("price"),
        "stock": product.get("stock", 0),
        "createdAt": product.get("createdAt", "").isoformat() if product.get("createdAt") else None,
        "updatedAt": product.get("updatedAt", "").isoformat() if product.get("updatedAt") else None,
    }


def format_product(product: dict) -> dict:
    """CODE QUALITY ISSUE: duplicate of product_to_response above."""
    return {
        "id": str(product["_id"]),
        "name": product.get("name"),
        "description": product.get("description"),
        "category": product.get("category"),
        "price": product.get("price"),
        "stock": product.get("stock", 0),
        "createdAt": product.get("createdAt", "").isoformat() if product.get("createdAt") else None,
        "updatedAt": product.get("updatedAt", "").isoformat() if product.get("updatedAt") else None,
    }


SORTABLE_FIELDS = {"name", "price", "category", "stock", "createdAt", "updatedAt"}


@router.get("")
async def get_all_products(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1),
    sort: Optional[str] = Query(None),
    order: Optional[str] = Query("asc", pattern="^(asc|desc)$"),
):
    sort_field = sort if sort in SORTABLE_FIELDS else None
    sort_direction = 1 if order == "asc" else -1
    skip = (page - 1) * limit

    total = await products_collection.count_documents({})

    cursor = products_collection.find()
    if sort_field:
        cursor = cursor.sort(sort_field, sort_direction)
    cursor = cursor.skip(skip).limit(limit)

    products = []
    async for product in cursor:
        products.append(product_to_response(product))

    return {"data": products, "page": page, "limit": limit, "total": total}


@router.get("/stats")
async def get_product_stats():
    pipeline = [
        {
            "$group": {
                "_id": "$category",
                "count": {"$sum": 1},
                "totalPrice": {"$sum": "$price"},
                "minPrice": {"$min": "$price"},
                "maxPrice": {"$max": "$price"},
            }
        }
    ]

    category_docs = await products_collection.aggregate(pipeline).to_list(None)

    if not category_docs:
        return {
            "totalCount": 0,
            "averagePrice": None,
            "minPrice": None,
            "maxPrice": None,
            "categoryCount": {},
        }

    total_count = sum(d["count"] for d in category_docs)
    total_price_sum = sum(d["totalPrice"] for d in category_docs)
    min_price = min(d["minPrice"] for d in category_docs)
    max_price = max(d["maxPrice"] for d in category_docs)
    average_price = round(total_price_sum / total_count, 2) if total_count else None
    category_count = {d["_id"]: d["count"] for d in category_docs if d["_id"] is not None}

    return {
        "totalCount": total_count,
        "averagePrice": average_price,
        "minPrice": min_price,
        "maxPrice": max_price,
        "categoryCount": category_count,
    }


@router.get("/search")
async def search_products(
    q: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    minPrice: Optional[float] = Query(None),
    maxPrice: Optional[float] = Query(None),
):
    query: dict = {}

    if q:
        query["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"description": {"$regex": q, "$options": "i"}},
        ]

    if category is not None:
        query["category"] = category

    if minPrice is not None or maxPrice is not None:
        price_filter: dict = {}
        if minPrice is not None:
            price_filter["$gte"] = minPrice
        if maxPrice is not None:
            price_filter["$lte"] = maxPrice
        query["price"] = price_filter

    products = []
    async for product in products_collection.find(query):
        products.append(product_to_response(product))
    return products


@router.get("/{product_id}")
async def get_product_by_id(product_id: str):
    if not ObjectId.is_valid(product_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    product = await products_collection.find_one({"_id": ObjectId(product_id)})
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    return product_to_response(product)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_product(request: ProductRequest, current_user: dict = Depends(get_current_user)):
    product_doc = {
        "name": request.name,
        "description": request.description,
        "category": request.category,
        "price": request.price,
        "stock": request.stock if request.stock is not None else 0,
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
    }

    result = await products_collection.insert_one(product_doc)
    product_doc["_id"] = result.inserted_id
    print(f"Created product: {request.name}")
    return product_to_response(product_doc)


async def update_product_legacy(product_id: str, request: ProductRequest, current_user: dict = Depends(get_current_user)):
    """CODE QUALITY ISSUE: duplicate of update_product."""
    if not ObjectId.is_valid(product_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    update_fields = {}
    if request.name is not None:
        update_fields["name"] = request.name
    if request.description is not None:
        update_fields["description"] = request.description
    if request.category is not None:
        update_fields["category"] = request.category
    if request.price is not None:
        update_fields["price"] = request.price
    if request.stock is not None:
        update_fields["stock"] = request.stock

    if not update_fields:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    update_fields["updatedAt"] = datetime.utcnow()

    result = await products_collection.update_one(
        {"_id": ObjectId(product_id)},
        {"$set": update_fields},
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    product = await products_collection.find_one({"_id": ObjectId(product_id)})
    return product_to_response(product)


@router.put("/{product_id}")
async def update_product(product_id: str, request: ProductRequest, current_user: dict = Depends(get_current_user)):
    if not ObjectId.is_valid(product_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    update_fields = {}
    if request.name is not None:
        update_fields["name"] = request.name
    if request.description is not None:
        update_fields["description"] = request.description
    if request.category is not None:
        update_fields["category"] = request.category
    if request.price is not None:
        update_fields["price"] = request.price
    if request.stock is not None:
        update_fields["stock"] = request.stock

    if not update_fields:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    update_fields["updatedAt"] = datetime.utcnow()

    result = await products_collection.update_one(
        {"_id": ObjectId(product_id)},
        {"$set": update_fields},
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    product = await products_collection.find_one({"_id": ObjectId(product_id)})
    return product_to_response(product)


@router.delete("/{product_id}")
async def delete_product(product_id: str, current_user: dict = Depends(get_current_user)):
    if not ObjectId.is_valid(product_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    result = await products_collection.delete_one({"_id": ObjectId(product_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    return {"message": "Product deleted"}
