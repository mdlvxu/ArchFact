from app.models.schemas import PostProcessingRuleSpec
from app.services.post_processor import PostProcessor


def test_deterministic_post_processing() -> None:
    records = [
        {
            "fields": {
                "measurement": {
                    "raw_value": "高  12厘米，口径8公分。",
                    "value": "高  12厘米，口径8公分。",
                    "status": "valid",
                    "evidence": [],
                }
            }
        }
    ]
    rules = [
        PostProcessingRuleSpec(key="space_removal", name="空格清理"),
        PostProcessingRuleSpec(key="punctuation_normalization", name="标点统一"),
        PostProcessingRuleSpec(key="unit_standardization", name="单位统一"),
    ]

    result = PostProcessor().apply(records, rules)

    assert result[0]["fields"]["measurement"]["value"] == "高 12 cm,口径8 cm."


def test_chinese_number_and_date_post_processing() -> None:
    records = [
        {
            "fields": {
                "description": {
                    "raw_value": "共一百零二件，记录于二〇二六年七月十五日",
                    "value": "共一百零二件，记录于二〇二六年七月十五日",
                    "status": "valid",
                    "evidence": [],
                }
            }
        }
    ]
    rules = [
        PostProcessingRuleSpec(key="chinese_number_to_arabic", name="中文数字转换"),
        PostProcessingRuleSpec(key="date_formatting", name="日期格式化"),
    ]

    result = PostProcessor().apply(records, rules)

    assert result[0]["fields"]["description"]["value"] == "共102件，记录于2026-07-15"
