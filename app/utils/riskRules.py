import json
import re
import sqlglot
from sqlglot import exp
from typing import Any, Optional


class PostgresRiskCheckerAST:
    RISK_LEVEL_MAP = {
        "low": {"name": "低风险", "sign": "🟢"},
        "medium": {"name": "中风险", "sign": "🟡"},
        "high": {"name": "高风险", "sign": "🔴"},
    }
    RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

    # Postgres 高危运维指令
    DANGER_ADMIN_CMD = [
        "VACUUM",
        "VACUUM ANALYZE",
        "REINDEX",
        "CLUSTER",
        "COPY",
    ]

    PERFORMANCE_RULES: dict[str, Any] = {
        "select_all_pattern": re.compile(r"SELECT\s+\*", re.IGNORECASE),
        "limit_pattern": re.compile(r"\bLIMIT\s+(\d+)", re.IGNORECASE),
        "join_keywords": ["JOIN", "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "FULL JOIN"],
        "group_by_pattern": re.compile(r"\bGROUP BY\b", re.IGNORECASE),
        "order_by_pattern": re.compile(r"\bORDER BY\b", re.IGNORECASE),
    }

    def __init__(
        self,
        allow_ddl: bool = False,  # 是否允许 DDL：CREATE / ALTER / DROP / RENAME
        allow_dml_no_where: bool = False,  # 是否允许不带 WHERE 的 UPDATE / DELETE
        allow_vacuum: bool = False,  # 是否允许 VACUUM / VACUUM ANALYZE，大量IO
        allow_reindex: bool = False,  # 是否允许 REINDEX，锁表/IO压力大
        allow_cluster: bool = False,  # 是否允许 CLUSTER，重排表数据，高危
        business_tables: Optional[list[str]] = None,
        critical_tables: Optional[list[str]] = None,
        forbidden_tables: Optional[list[str]] = None,
        select_scan_rows_warn_threshold: int = 10000,
        select_scan_rows_critical_threshold: int = 1000000,
        select_max_limit: int = 1000,
        dml_max_rows_warn_threshold: int = 1000,
        dml_max_rows_critical_threshold: int = 100000,
    ):
        """
        Postgres SQL风险检查器（仅三档风险：低/中/高）
        :param allow_ddl: 是否允许 CREATE / ALTER / DROP 等DDL
        :param allow_dml_no_where: 是否允许不带WHERE的UPDATE / DELETE
        :param allow_vacuum: 是否允许VACUUM / VACUUM ANALYZE
        :param allow_reindex: 是否允许REINDEX
        :param allow_cluster: 是否允许CLUSTER
        :param business_tables: 业务表列表（仅标记审计）
        :param critical_tables: 核心敏感表（命中告警，不拦截）
        :param forbidden_tables: 禁止操作黑名单表（命中直接拒绝）
        :param select_scan_rows_warn_threshold: select预估扫描行数警告阈值
        :param select_scan_rows_critical_threshold: select预估扫描行数高危阈值
        :param select_max_limit: limit最大允许值
        :param dml_max_rows_warn_threshold: dml影响行数警告阈值
        :param dml_max_rows_critical_threshold: dml影响行数高危阈值
        """
        self.allow_ddl = allow_ddl
        self.allow_dml_no_where = allow_dml_no_where
        self.allow_vacuum = allow_vacuum
        self.allow_reindex = allow_reindex
        self.allow_cluster = allow_cluster

        self.business_tables = set(t.lower() for t in (business_tables or []))
        self.critical_tables = set(t.lower() for t in (critical_tables or []))
        self.forbidden_tables = set(t.lower() for t in (forbidden_tables or []))

        self.select_scan_rows_warn_threshold = select_scan_rows_warn_threshold
        self.select_scan_rows_critical_threshold = select_scan_rows_critical_threshold
        self.select_max_limit = select_max_limit
        self.dml_max_rows_warn_threshold = dml_max_rows_warn_threshold
        self.dml_max_rows_critical_threshold = dml_max_rows_critical_threshold

        self.RULES: dict[Any, Any] = {
            exp.Select: (
                "low",
                "只读查询，无数据变更风险；注意无索引大查询可能引发性能雪崩",
            ),
            exp.Insert: ("medium", "插入数据；批量插入会增大库压力"),
            exp.Merge: ("medium", "Upsert合并操作；匹配条件错误会批量误写"),
            exp.Update: ("medium", "更新数据；若无WHERE条件，升级为高风险"),
            exp.Delete: ("medium", "删除数据；若无WHERE条件，升级为高风险"),
            exp.Create: None,
            exp.Alter: ("high", "表结构变更；PG很多ALTER会锁表，阻塞读写"),
            exp.Drop: None,
        }

    def check(self, sql: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": 0,
            "message": "ok",
            "data": {
                "sql": sql.strip(),
                "parse_ok": False,
                "overall_risk_level": "low",
                "overall_risk_name": "低风险",
                "overall_risk_sign": "🟢",
                "risk_desc": "未识别高危操作",
                "action": "pass",
                "alert": "",
                "warnings": [],
                "errors": [],
                "tables": [],
                "statements": [],
                "performance_risk": {
                    "risk_level": "low",
                    "estimate_scan_rows": None,
                    "estimate_rows_msg": "仅启发式估算，无真实EXPLAIN",
                    "select_all": False,
                    "has_limit": False,
                    "limit_value": None,
                    "has_join": False,
                    "has_group_by": False,
                    "has_order_by": False,
                },
                "table_check": {
                    "business_tables": [],
                    "critical_tables": [],
                    "forbidden_tables": [],
                },
                "config_snapshot": self._config_snapshot(),
            },
        }

        if not sql or not sql.strip():
            result["code"] = 400
            result["message"] = "empty_sql"
            result["data"]["errors"].append("SQL为空，无法检查")
            result["data"]["action"] = "reject"
            return result

        raw_check = self._check_raw_postgres_risk(sql)
        if raw_check:
            return self._merge_raw_result(result, raw_check)

        try:
            expressions = sqlglot.parse(sql, read="postgres")
        except Exception as e:
            result["code"] = 500
            result["message"] = "parse_error"
            result["data"]["errors"].append(f"SQL解析失败: {str(e)}")
            result["data"]["action"] = "reject"
            result["data"]["risk_desc"] = "SQL语法异常，强制人工复核"
            return result

        if not expressions:
            result["code"] = 400
            result["message"] = "no_statement"
            result["data"]["errors"].append("未识别有效SQL语句")
            result["data"]["action"] = "reject"
            return result

        result["data"]["parse_ok"] = True
        overall_risk = "low"
        overall_desc = ""
        tables: set[Any] = set()
        statements_info: list[Any] = []
        alerts: list[Any] = []
        warnings: list[Any] = []
        errors: list[Any] = []

        for expr in expressions:
            stmt_result = self._analyze_statement(expr)
            statements_info.append(stmt_result)
            for t in stmt_result.get("tables", []):
                tables.add(t)
            if (
                self.RISK_ORDER[stmt_result["risk_level"]]
                > self.RISK_ORDER[overall_risk]
            ):
                overall_risk = stmt_result["risk_level"]
                overall_desc = stmt_result["risk_desc"]
            alerts.extend(stmt_result.get("alerts", []))
            warnings.extend(stmt_result.get("warnings", []))
            errors.extend(stmt_result.get("errors", []))

        result["data"]["statements"] = statements_info
        result["data"]["tables"] = sorted(tables)
        table_check = self._check_tables(tables)
        result["data"]["table_check"] = table_check
        if table_check["forbidden_tables"]:
            errors.append(f"操作禁止表: {table_check['forbidden_tables']}")
        if table_check["critical_tables"]:
            warnings.append(f"操作核心敏感表: {table_check['critical_tables']}")

        perf_risk = self._check_performance(sql, expressions)
        result["data"]["performance_risk"] = perf_risk
        if perf_risk["risk_level"] != "low":
            warnings.append(perf_risk.get("risk_msg", "存在性能风险"))

        result["data"]["overall_risk_level"] = overall_risk
        result["data"]["overall_risk_name"] = self.RISK_LEVEL_MAP[overall_risk]["name"]
        result["data"]["overall_risk_sign"] = self.RISK_LEVEL_MAP[overall_risk]["sign"]
        result["data"]["risk_desc"] = overall_desc

        if errors:
            result["data"]["action"] = "reject"
        elif overall_risk == "high" or any("reject" in a for a in alerts):
            result["data"]["action"] = "reject"
        elif overall_risk == "medium" or warnings:
            result["data"]["action"] = "review"
        else:
            result["data"]["action"] = "pass"

        result["data"]["alert"] = " | ".join(alerts) if alerts else ""
        result["data"]["warnings"] = warnings
        result["data"]["errors"] = errors

        if result["data"]["action"] == "reject":
            result["code"] = 403
            result["message"] = "risk_rejected"
        elif result["data"]["action"] == "review":
            result["code"] = 200
            result["message"] = "need_review"
        else:
            result["code"] = 0
            result["message"] = "pass"
        return result

    def _analyze_statement(self, expr: Any) -> dict[str, Any]:
        res: dict[str, Any] = {
            "statement_type": type(expr).__name__,
            "tables": [],
            "risk_level": "low",
            "risk_desc": "",
            "alerts": [],
            "warnings": [],
            "errors": [],
        }
        # 识别 EXPLAIN
        if expr.args.get("explain") is True:
            res["risk_level"] = "low"
            res["risk_desc"] = "执行计划分析（EXPLAIN）"
            child = expr.args.get("this")
            if child:
                res["tables"] = self.extract_table_names(child)
            return res

        # 识别事务 Transaction：BEGIN / COMMIT / ROLLBACK / SAVEPOINT
        if isinstance(expr, exp.Transaction):
            kind = expr.args.get("kind", "").upper()
            if kind == "BEGIN":
                res["risk_level"] = "low"
                res["risk_desc"] = "开启事务"
            elif kind == "COMMIT":
                res["risk_level"] = "medium"
                res["risk_desc"] = "提交事务，变更持久化"
            elif kind == "ROLLBACK":
                res["risk_level"] = "low"
                res["risk_desc"] = "回滚事务"
            elif kind == "SAVEPOINT":
                res["risk_level"] = "low"
                res["risk_desc"] = "事务保存点"
            return res

        # Alter 里面识别 RENAME TABLE
        if isinstance(expr, exp.Alter):
            alter_kind = expr.args.get("kind", "").upper()
            if alter_kind == "RENAME":
                res["risk_level"] = "high"
                res["risk_desc"] = "重命名表；视图、外键、业务代码会失效"
                res["tables"] = self.extract_table_names(expr)
                if not self.allow_ddl:
                    res["errors"].append("DDL操作不允许")
                    res["alerts"].append("⚠️ DDL已禁用")
                res["alerts"].append("⚠️ 高危操作，必须审批")
                return res

        res["tables"] = self.extract_table_names(expr)
        cls = type(expr)
        if cls not in self.RULES:
            res["risk_level"] = "medium"
            res["risk_desc"] = f"未内置规则语句 {cls.__name__}，建议人工审核"
            res["warnings"].append(f"未内置规则语句: {cls.__name__}")
            return res
        base_rule = self.RULES[cls]
        if cls == exp.Create:
            level, desc = self._judge_create(expr)
        elif cls == exp.Drop:
            level, desc = self._judge_drop(expr)
        elif cls in (exp.Update, exp.Delete):
            level, desc = base_rule
            if not self.has_where_in_ast(expr):
                level = "high"
                desc = f"{cls.__name__} 无WHERE条件，全表操作"
                if not self.allow_dml_no_where:
                    res["errors"].append("UPDATE/DELETE缺少WHERE条件，已拒绝")
                    res["alerts"].append("⚠️ 告警：缺少WHERE条件！禁止直接执行！")
            else:
                res["warnings"].append(f"{cls.__name__} 携带WHERE，建议复核过滤范围")
        else:
            level, desc = base_rule

        switch_check = self._check_ddl_switch(cls, expr)
        if switch_check:
            res["errors"].extend(switch_check["errors"])
            res["alerts"].extend(switch_check["alerts"])
            if switch_check["force_reject"]:
                level = "high"
        res["risk_level"] = level
        res["risk_desc"] = desc
        if level == "high":
            res["alerts"].append("⚠️ 高危操作，必须审批")
        return res

    def _judge_create(self, expr: exp.Create):
        kind = expr.args.get("kind", "").upper()
        if kind == "TABLE":
            return "medium", "新建数据表"
        elif kind == "INDEX":
            return "high", "创建索引，PG大表建索引会锁表/消耗大量IO"
        elif kind == "VIEW":
            return "medium", "创建视图，复杂视图存在性能隐患"
        elif kind in ("TRIGGER", "FUNCTION", "PROCEDURE"):
            return "high", "触发器/函数/存储过程，逻辑隐患大"
        return "medium", "CREATE未知子类型，请人工复核"

    def _judge_drop(self, expr: exp.Drop):
        kind = expr.args.get("kind", "").upper()
        if kind == "TABLE":
            return "high", "删除表，数据无法恢复，连带外键、视图失效"
        elif kind == "INDEX":
            return "high", "删除索引，影响查询性能"
        elif kind == "VIEW":
            return "medium", "删除视图"
        elif kind == "TRIGGER":
            return "medium", "删除触发器"
        elif kind in ("FUNCTION", "PROCEDURE"):
            return "medium", "删除函数/存储过程"
        return "high", "DROP未知子类型，请人工复核"

    def _check_ddl_switch(self, cls: Any, expr: Any) -> dict[str, Any]:
        ret: dict[str, Any] = {"errors": [], "alerts": [], "force_reject": False}
        if cls in (exp.Create, exp.Alter, exp.Drop):
            if not self.allow_ddl:
                ret["errors"].append("DDL操作不允许")
                ret["alerts"].append("⚠️ DDL已禁用")
                ret["force_reject"] = True
        return ret

    def _check_tables(self, tables: set[str]) -> dict[str, Any]:
        business: list[Any] = []
        critical: list[Any] = []
        forbidden: list[Any] = []
        for t in tables:
            tl = t.lower()
            if tl in self.forbidden_tables:
                forbidden.append(t)
            elif tl in self.critical_tables:
                critical.append(t)
            elif tl in self.business_tables:
                business.append(t)
        return {
            "business_tables": business,
            "critical_tables": critical,
            "forbidden_tables": forbidden,
        }

    def _check_performance(self, sql: str, expressions: list[Any]) -> dict[str, Any]:
        perf: dict[str, Any] = {
            "risk_level": "low",
            "estimate_scan_rows": None,
            "estimate_rows_msg": "仅启发式估算，无真实EXPLAIN",
            "select_all": False,
            "has_limit": False,
            "limit_value": None,
            "has_join": False,
            "has_group_by": False,
            "has_order_by": False,
            "risk_msg": "",
        }
        upper_sql = " ".join(sql.split())
        if self.PERFORMANCE_RULES["select_all_pattern"].search(upper_sql):
            perf["select_all"] = True
            perf["risk_msg"] = "使用SELECT *，存在性能风险"
        limit_match = self.PERFORMANCE_RULES["limit_pattern"].search(upper_sql)
        if limit_match:
            perf["has_limit"] = True
            perf["limit_value"] = int(limit_match.group(1))
            if perf["limit_value"] > self.select_max_limit:
                perf["risk_level"] = "medium"
                perf["risk_msg"] = (
                    f"LIMIT {perf['limit_value']} 超过阈值 {self.select_max_limit}"
                )
        for kw in self.PERFORMANCE_RULES["join_keywords"]:
            if kw in upper_sql:
                perf["has_join"] = True
                break
        if self.PERFORMANCE_RULES["group_by_pattern"].search(upper_sql):
            perf["has_group_by"] = True
        if self.PERFORMANCE_RULES["order_by_pattern"].search(upper_sql):
            perf["has_order_by"] = True
        perf["estimate_scan_rows"] = self._estimate_scan_rows(sql, expressions)
        est: Any = perf["estimate_scan_rows"]
        if est is not None:
            if est >= self.select_scan_rows_critical_threshold:
                perf["risk_level"] = "high"
                perf["risk_msg"] = f"预估扫描行数 {est} 超过临界阈值"
            elif est >= self.select_scan_rows_warn_threshold:
                perf["risk_level"] = "medium"
                perf["risk_msg"] = f"预估扫描行数 {est} 超过警告阈值"
        return perf

    def _estimate_scan_rows(self, sql: str, expressions: list[Any]):
        score = 0
        has_where = any(expr.find(exp.Where) for expr in expressions)
        table_count = sum(len(self.extract_table_names(expr)) for expr in expressions)
        upper_sql = " ".join(sql.split())
        join_count = sum(
            upper_sql.count(k) for k in self.PERFORMANCE_RULES["join_keywords"]
        )
        if not has_where:
            score += 50000
        score += table_count * 8000
        score += join_count * 15000
        limit_match = self.PERFORMANCE_RULES["limit_pattern"].search(upper_sql)
        if limit_match:
            limit_val = int(limit_match.group(1))
            score = min(score, limit_val * 20)
        return score

    def _check_raw_postgres_risk(self, sql: str) -> Optional[dict[str, Any]]:
        upper_sql = " ".join(sql.split()).upper()
        # 高危运维命令
        if "VACUUM ANALYZE" in upper_sql:
            return self._build_raw_result(
                "high", "VACUUM ANALYZE，大量IO消耗", self.allow_vacuum
            )
        elif "VACUUM" in upper_sql:
            return self._build_raw_result(
                "high", "VACUUM 回收表空间，IO压力大", self.allow_vacuum
            )
        if "REINDEX" in upper_sql:
            return self._build_raw_result(
                "high", "REINDEX 重建索引，锁表", self.allow_reindex
            )
        if "CLUSTER" in upper_sql:
            return self._build_raw_result(
                "high", "CLUSTER 按索引重排表，重写全表", self.allow_cluster
            )
        if "COPY" in upper_sql:
            return self._build_raw_result(
                "high", "COPY 命令，可读写文件/批量导入导出", False
            )
        return None

    def _build_raw_result(self, level: str, desc: str, allowed: bool) -> dict[str, Any]:
        return {
            "code": 0 if allowed else 403,
            "message": "pass" if allowed else "risk_rejected",
            "data": {
                "parse_ok": True,
                "overall_risk_level": level,
                "overall_risk_name": self.RISK_LEVEL_MAP[level]["name"],
                "overall_risk_sign": self.RISK_LEVEL_MAP[level]["sign"],
                "risk_desc": desc,
                "action": "pass" if allowed else "reject",
                "alert": f"⚠️ {desc}",
                "warnings": [] if allowed else [desc],
                "errors": [] if allowed else [f"{desc}，当前环境未允许执行"],
                "tables": [],
                "statements": [],
            },
        }

    def _merge_raw_result(
        self, result: dict[str, Any], raw: dict[str, Any]
    ) -> dict[str, Any]:
        result.update(raw)
        return result

    def has_where_in_ast(self, expr: Any) -> bool:
        return expr.find(exp.Where) is not None

    def extract_table_names(self, expr: Any) -> list[str]:
        if expr is None:
            return []
        tables: list[Any] = []
        for tbl in expr.find_all(exp.Table):
            tables.append(tbl.name)
        return list(set(tables))

    def _config_snapshot(self) -> dict[str, Any]:
        return {
            "allow_ddl": self.allow_ddl,
            "allow_dml_no_where": self.allow_dml_no_where,
            "allow_vacuum": self.allow_vacuum,
            "allow_reindex": self.allow_reindex,
            "allow_cluster": self.allow_cluster,
            "business_tables": sorted(self.business_tables),
            "critical_tables": sorted(self.critical_tables),
            "forbidden_tables": sorted(self.forbidden_tables),
            "select_scan_rows_warn_threshold": self.select_scan_rows_warn_threshold,
            "select_scan_rows_critical_threshold": self.select_scan_rows_critical_threshold,
            "select_max_limit": self.select_max_limit,
            "dml_max_rows_warn_threshold": self.dml_max_rows_warn_threshold,
            "dml_max_rows_critical_threshold": self.dml_max_rows_critical_threshold,
        }

    def check_json(self, sql: str, indent: int = 2) -> str:
        return json.dumps(self.check(sql), ensure_ascii=False, indent=indent)


# ------------------------ 示例调用 ------------------------
if __name__ == "__main__":
    checker = PostgresRiskCheckerAST(
        allow_ddl=False,
        allow_dml_no_where=False,
        allow_vacuum=False,
        allow_reindex=False,
        allow_cluster=False,
        business_tables=["user", "order_info"],
        critical_tables=["account", "balance"],
        forbidden_tables=["backup_data"],
        select_scan_rows_warn_threshold=10000,
        select_scan_rows_critical_threshold=1000000,
        select_max_limit=1000,
    )

    test_sqls = [
        "select * from user limit 100;",
        "update user set name='a' where id=1;",
        "update user set name='a';",
        "delete from order_info;",
        "vacuum;",
        "vacuum analyze user;",
        "reindex user;",
        "cluster user using idx_user_id;",
        "copy user to '/tmp/user.csv';",
        "drop table backup_data;",
        "alter table user add column age int;",
        "alter table user rename to user_new;",
        "begin;",
        "commit;",
        "rollback;",
        "savepoint sp1;",
        "explain select * from user;",
    ]
    for s in test_sqls:
        print("=" * 90)
        print("SQL:", s)
        print(checker.check_json(s))
