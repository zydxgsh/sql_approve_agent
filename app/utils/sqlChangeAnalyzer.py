import sqlglot
from sqlglot import exp
from typing import Dict, Any, Optional, List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlglot.expressions import Expr
else:
    Expr = object


class SqlChangeAnalyzer:
    def __init__(self) -> None:
        pass

    @staticmethod
    def _is_node(node: Expr, cls_name: str) -> bool:
        return node.__class__.__name__ == cls_name

    @staticmethod
    def _safe_get_table_identifier(table_exp: Optional[Expr]) -> str:
        """安全获取 schema.table，增加空判断，避免 .db 报错"""
        if not isinstance(table_exp, exp.Table):
            # return "unknown"
            return ""
        schema = table_exp.db
        table = table_exp.name
        return f"{schema or 'public'}.{table}"

    def analyze_sql(self, sql: str) -> Dict[str, Any]:
        """
        :param sql: 多条SQL用;分隔
        :return:
            {
                "op_type": str,
                "target_table": str,
                "effect_desc": str,
                "reversible": str,
                "detail_list": List[Dict]
            }
        """
        try:
            expr_list: List[Optional[Expr]] = sqlglot.parse(sql, read="postgres")
        except Exception:
            return {"code": -1, "msg": "SQL解析失败"}

        if not expr_list:
            return {"code": -1, "msg": "SQL为空"}

        items: List[Dict[str, str]] = []
        has_unknown: bool = False

        for expr in expr_list:
            if expr is None:
                continue
            sub_items, unknown = self._parse_single_expr(expr)
            items.extend(sub_items)
            if unknown:
                has_unknown = True

        if len(items) == 0:
            return {"code": -1, "msg": "不支持该SQL操作类型"}

        summary: Dict[str, str] = self._merge_items(items, has_unknown)
        summary["detail_list"] = items
        return summary

    def _parse_single_expr(self, expr: Expr) -> Tuple[List[Dict[str, str]], bool]:
        sub_items: List[Dict[str, str]] = []
        unknown: bool = False

        if self._is_node(expr, "Alter"):
            tbl_exp: Expr = expr.this
            for action in expr.actions:
                if self._is_node(action, "ColumnDef"):
                    sub_items.append(self._handle_add_column(action, tbl_exp))
                elif self._is_node(action, "AlterColumn"):
                    if action.args.get("drop") is True:
                        sub_items.append(self._handle_drop_column(action, tbl_exp))
                    elif action.args.get("kind") is not None:
                        sub_items.append(self._handle_column_type(action, tbl_exp))
                    else:
                        unknown = True
                elif self._is_node(action, "RenameColumn"):
                    sub_items.append(self._handle_rename_column(action, tbl_exp))
                else:
                    unknown = True
            return sub_items, unknown

        if self._is_node(expr, "Drop"):
            kind = getattr(expr, "kind", None)
            target = expr.this
            # Drop 可能是多个对象（逗号分隔），是Array类型
            if isinstance(target, exp.Array):
                for item in target.expressions:
                    if kind == "TABLE":
                        sub_items.append(self._handle_drop_table(item))
                    elif kind == "INDEX":
                        sub_items.append(self._handle_drop_index_single(item))
            else:
                if kind == "TABLE":
                    sub_items.append(self._handle_drop_table(target))
                elif kind == "INDEX":
                    sub_items.append(self._handle_drop_index(expr))
                else:
                    unknown = True
        elif self._is_node(expr, "CreateIndex"):
            sub_items.append(self._handle_create_index(expr))
        elif self._is_node(expr, "Truncate"):
            sub_items.append(self._handle_truncate(expr))
        else:
            unknown = True
        return sub_items, unknown

    def _merge_items(
        self, items: List[Dict[str, str]], has_unknown: bool
    ) -> Dict[str, str]:
        op_types: set[str] = set()
        target_tables: set[str] = set()
        effect_lines: List[str] = []
        rollback_sqls: List[str] = []
        irreversible_count: int = 0

        for it in items:
            op_types.add(it["op_type"])
            target_tables.add(it["target_table"])
            effect_lines.append(it["effect_desc"])
            rev: str = it["reversible"]
            if rev == "不可逆":
                irreversible_count += 1
            else:
                rollback_sqls.append(rev)

        op_type_text: str = "、".join(sorted(op_types))
        target_table_text: str = "、".join(sorted(target_tables))
        effect_text: str = "；".join(effect_lines)
        if has_unknown:
            effect_text += "【警告：部分SQL无法识别，请人工复核】"

        if irreversible_count > 0:
            reversible_text: str = (
                f"包含{irreversible_count}项不可逆操作，回滚语句：{'；'.join(rollback_sqls)}"
            )
        else:
            reversible_text = f"全部操作可回滚，回滚语句：{'；'.join(rollback_sqls)}"

        return {
            "op_type": op_type_text,
            "target_table": target_table_text,
            "effect_desc": effect_text,
            "reversible": reversible_text,
        }

    def _handle_add_column(self, action: Expr, tbl_exp: Expr) -> Dict[str, str]:
        tbl_id: str = self._safe_get_table_identifier(tbl_exp)
        col_name: str = action.name
        col_type: str = action.args.get("kind").sql()
        has_default: bool = action.args.get("default") is not None
        if has_default:
            effect: str = (
                f"新增字段 {col_name} {col_type} 并设置默认值；存量数据会被重写，属于高风险变更"
            )
        else:
            effect = (
                f"新增字段 {col_name} {col_type}；存量数据保持不变，不会改写原有数据"
            )
        return {
            "op_type": "新增字段",
            "target_table": tbl_id,
            "effect_desc": effect,
            "reversible": f"ALTER TABLE {tbl_id} DROP COLUMN IF EXISTS {col_name};",
        }

    def _handle_drop_column(self, action: Expr, tbl_exp: Expr) -> Dict[str, str]:
        tbl_id: str = self._safe_get_table_identifier(tbl_exp)
        col_name: str = action.this.name
        effect: str = f"删除字段 {col_name}；存量数据中该字段内容会被永久清除"
        return {
            "op_type": "删除字段",
            "target_table": tbl_id,
            "effect_desc": effect,
            "reversible": "不可逆",
        }

    def _handle_rename_column(self, action: Expr, tbl_exp: Expr) -> Dict[str, str]:
        tbl_id: str = self._safe_get_table_identifier(tbl_exp)
        old_name: str = action.this.name
        new_name: str = action.args.get("to").name
        effect: str = f"字段重命名，由 {old_name} 修改为 {new_name}，已有数据不受影响"
        return {
            "op_type": "字段重命名",
            "target_table": tbl_id,
            "effect_desc": effect,
            "reversible": f"ALTER TABLE {tbl_id} RENAME COLUMN {new_name} TO {old_name};",
        }

    def _handle_column_type(self, action: Expr, tbl_exp: Expr) -> Dict[str, str]:
        tbl_id: str = self._safe_get_table_identifier(tbl_exp)
        col_name: str = action.this.name
        new_type: str = action.args.get("kind").sql()
        effect: str = (
            f"修改字段 {col_name} 数据类型为 {new_type}；存量数据需要转换，存在转换失败风险"
        )
        return {
            "op_type": "修改字段类型",
            "target_table": tbl_id,
            "effect_desc": effect,
            "reversible": "需要手动编写反向DDL，部分类型转换不可逆",
        }

    def _handle_create_index(self, expr: Expr) -> Dict[str, str]:
        tbl_id: str = self._safe_get_table_identifier(expr.table)
        idx_name: str = expr.name
        effect: str = f"创建索引 {idx_name}；基于存量数据构建索引，执行期间可能持有表锁"
        return {
            "op_type": "创建索引",
            "target_table": tbl_id,
            "effect_desc": effect,
            "reversible": f"DROP INDEX IF EXISTS {idx_name};",
        }

    def _handle_drop_index(self, expr: Expr) -> Dict[str, str]:
        idx_name: str = expr.name
        effect: str = f"删除索引 {idx_name}；查询性能可能下降"
        return {
            "op_type": "删除索引",
            "target_table": "-",
            "effect_desc": effect,
            "reversible": "不可逆",
        }

    def _handle_drop_index_single(self, item: Expr) -> Dict[str, str]:
        idx_name: str = item.name
        effect: str = f"删除索引 {idx_name}；查询性能可能下降"
        return {
            "op_type": "删除索引",
            "target_table": "-",
            "effect_desc": effect,
            "reversible": "不可逆",
        }

    def _handle_drop_table(self, table_exp: Expr) -> Dict[str, str]:
        tbl_id: str = self._safe_get_table_identifier(table_exp)
        effect: str = f"删除表 {tbl_id}；表结构和全部数据都会被清除，关联视图、外键失效"
        return {
            "op_type": "删除表",
            "target_table": tbl_id,
            "effect_desc": effect,
            "reversible": "不可逆",
        }

    def _handle_truncate(self, expr: Expr) -> Dict[str, str]:
        tbl_id: str = self._safe_get_table_identifier(expr.this)
        effect: str = f"清空表 {tbl_id}；表内所有数据清除，表结构保留"
        return {
            "op_type": "清空表",
            "target_table": tbl_id,
            "effect_desc": effect,
            "reversible": "不可逆",
        }


# ========= 本地测试 =========
if __name__ == "__main__":
    analyzer = SqlChangeAnalyzer()
    test_sql = "DROP TABLE IF EXISTS demo_business.users;"
    res = analyzer.analyze_sql(test_sql)
    import json

    print(json.dumps(res, ensure_ascii=False, indent=2))
