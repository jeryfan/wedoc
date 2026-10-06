"""Template list serialization int-ifies a whole-number order (JS number parity,
matching the base-node fix)."""

from wedoc.modules.template.service import TemplateService


def test_transform_list_item_order_is_int():
    svc = TemplateService()
    row = {
        "id": "tplX",
        "name": "T",
        "order": 1.0,
        "createdBy": "usrX",
    }
    user_map = {"usrX": {"id": "usrX", "name": "U", "email": "u@example.com"}}
    item = svc._transform_list_item(row, user_map, ("id", "name", "order", "createdBy"))
    assert item["order"] == 1
    assert isinstance(item["order"], int)


def test_transform_list_item_fractional_order_kept():
    svc = TemplateService()
    row = {"id": "tplY", "name": "T", "order": 1.5, "createdBy": "usrX"}
    user_map = {"usrX": {"id": "usrX", "name": "U", "email": "u@example.com"}}
    item = svc._transform_list_item(row, user_map, ("id", "name", "order", "createdBy"))
    assert item["order"] == 1.5
