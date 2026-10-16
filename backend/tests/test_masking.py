"""Tests for the masking engine (Phase 10)."""

import pytest

from app.agents.masking import (
    MaskingContext,
    generate_salt,
    generate_token_key,
    mask_value,
)
from app.agents.masking_orchestrator import (
    MaskingOrchestrator,
    MaskingOrchestratorError,
)
from app.database.session import session_scope
from app.models import Base
from app.models.entities import (
    Approval,
    ClassificationResult,
    Connection,
    DiscoveredColumn,
    DiscoveredForeignKey,
    DiscoveredTable,
    Job,
    Server,
    User,
)


def _prepare(monkeypatch, tmp_path):
    db = tmp_path / "mask.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db}")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "O7ZpK9d3v8s2eRfTjYwHxLqBnVcMgUaI0pEhKlS9XzAo=")
    from app.core import config as cfg
    from app.database import session as sess

    cfg.get_settings.cache_clear()
    sess.get_engine.cache_clear()
    sess.get_session_factory.cache_clear()
    Base.metadata.create_all(sess.get_engine())


# ---------- strategy unit tests ----------


def test_null_strategy_returns_none():
    ctx = MaskingContext("j", generate_salt(), generate_token_key())
    assert mask_value("anything", "Null", "col", ctx) is None
    assert mask_value("anything", "Null", "col", ctx) is None


def test_fake_name_strategy_returns_name():
    ctx = MaskingContext("j", generate_salt(), generate_token_key())
    out = mask_value("Ada Lovelace", "Fake Name", "FullName", ctx)
    assert isinstance(out, str)
    assert " " in out
    assert out != "Ada Lovelace"


def test_fake_email_strategy():
    ctx = MaskingContext("j", generate_salt(), generate_token_key())
    out = mask_value("ada@example.com", "Fake Email", "Email", ctx)
    assert "@" in out
    assert out != "ada@example.com"


def test_masked_phone_keeps_last_four():
    ctx = MaskingContext("j", generate_salt(), generate_token_key())
    assert mask_value("+1 (555) 123-4567", "Masked Phone", "Phone", ctx) == "*******4567"
    assert mask_value("1234", "Masked Phone", "Phone", ctx) == "1234"
    assert mask_value("12", "Masked Phone", "Phone", ctx) == "****"


def test_hash_strategy_is_deterministic_per_job():
    ctx = MaskingContext("j", generate_salt(), generate_token_key())
    a = mask_value("ada@example.com", "Hash", "Email", ctx)
    b = mask_value("ada@example.com", "Hash", "Email", ctx)
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_hash_strategy_differs_across_jobs():
    a = mask_value("x", "Hash", "C", MaskingContext("j1", generate_salt(), generate_token_key()))
    b = mask_value("x", "Hash", "C", MaskingContext("j2", generate_salt(), generate_token_key()))
    # Different jobs have different salts by default; the hash MUST differ.
    assert a != b


def test_tokenization_is_deterministic_and_reversible_via_key():
    salt = generate_salt()
    tk = generate_token_key()
    ctx = MaskingContext("j", salt, tk)
    tok = mask_value("ada@example.com", "Tokenization", "Email", ctx)
    assert len(tok) == 64
    assert tok == mask_value("ada@example.com", "Tokenization", "Email", ctx)
    # A fresh context with a different key produces a different token.
    other = MaskingContext("j", salt, generate_token_key())
    assert mask_value("ada@example.com", "Tokenization", "Email", other) != tok


def test_preserve_format_keeps_tail():
    ctx = MaskingContext("j", generate_salt(), generate_token_key())
    assert mask_value("ABCDEFGH", "Preserve Format", "C", ctx) == "****EFGH"
    assert mask_value("AB", "Preserve Format", "C", ctx) == "**"


def test_random_replacement_returns_new_value_of_same_type():
    ctx = MaskingContext("j", generate_salt(), generate_token_key())
    assert isinstance(mask_value(123, "Random Replacement", "C", ctx), int)
    assert isinstance(mask_value(1.5, "Random Replacement", "C", ctx), float)
    out = mask_value("hello", "Random Replacement", "C", ctx)
    assert isinstance(out, str) and out != "hello"


def test_custom_rule_invocation():
    ctx = MaskingContext("j", generate_salt(), generate_token_key())

    def upper(value, _ctx, _col):
        return str(value).upper()

    ctx.register_custom_rule("MyCol", upper)
    assert mask_value("abc", "Custom Rule", "MyCol", ctx) == "ABC"


def test_custom_rule_without_registration_raises():
    ctx = MaskingContext("j", generate_salt(), generate_token_key())
    from app.agents.masking import MaskingError

    with pytest.raises(MaskingError):
        mask_value("x", "Custom Rule", "NotRegistered", ctx)


def test_unknown_strategy_raises():
    ctx = MaskingContext("j", generate_salt(), generate_token_key())
    from app.agents.masking import MaskingError

    with pytest.raises(MaskingError):
        mask_value("x", "Telepathy", "C", ctx)


def test_value_none_propagates_for_all_strategies():
    for strat in [
        "Fake Name",
        "Fake Email",
        "Masked Phone",
        "Hash",
        "Tokenization",
        "Preserve Format",
        "Random Replacement",
        "Null",
        "LLM Anonymize",
    ]:
        ctx = MaskingContext("j", generate_salt(), generate_token_key())
        assert mask_value(None, strat, "C", ctx) is None


def test_llm_anonymize_fallback_passthrough():
    """LLM Anonymize without a warmed cache returns value unchanged."""
    ctx = MaskingContext("j", generate_salt(), generate_token_key())
    result = mask_value("secret", "LLM Anonymize", "C", ctx)
    assert result == "secret"


# ---------- orchestrator tests ----------


def _seed_orchestrator(monkeypatch, tmp_path, fk=True):
    _prepare(monkeypatch, tmp_path)
    with session_scope() as session:
        u = User(username="m", password_hash="!x", is_active=True)
        session.add(u)
        session.flush()
        sv = Server(name="s", host="h", port=1, auth_type="sql")
        session.add(sv)
        session.flush()
        c = Connection(server_id=sv.id, database_name="d", encrypted_connection_string="!x")
        session.add(c)
        session.flush()
        j = Job(connection_id=c.id, requested_by_user_id=u.id, status="running", workflow_state={})
        session.add(j)
        session.flush()
        customers = DiscoveredTable(
            job_id=j.id, schema_name="dbo", table_name="Customers", object_type="BASE TABLE"
        )
        orders = DiscoveredTable(
            job_id=j.id, schema_name="dbo", table_name="Orders", object_type="BASE TABLE"
        )
        session.add_all([customers, orders])
        session.flush()
        cols = {}
        for t, lst in [
            (customers, [("Id", "int", True), ("Email", "nvarchar", False)]),
            (orders, [("Id", "int", True), ("CustomerId", "int", False)]),
        ]:
            for i, (n, dt, is_pk) in enumerate(lst, start=1):
                col = DiscoveredColumn(
                    table_id=t.id,
                    name=n,
                    ordinal_position=i,
                    data_type=dt,
                    is_nullable=True,
                    is_primary_key=is_pk,
                )
                session.add(col)
                session.flush()
                cols[(t.table_name, n)] = col
        # Approve Email with Fake Email, Id (PK) with Hash.
        for (tn, cn), strat in [
            (("Customers", "Email"), "Fake Email"),
            (("Customers", "Id"), "Hash"),
        ]:
            cls = ClassificationResult(
                job_id=j.id,
                schema_name="dbo",
                table_name=tn,
                column_name=cn,
                sensitivity="Email" if strat == "Fake Email" else "Non Sensitive",
                confidence=0.9,
                reasoning="r",
                suggested_strategy=strat,
                attempts=1,
                ok=True,
            )
            session.add(cls)
            session.flush()
            session.add(
                Approval(
                    classification_result_id=cls.id,
                    approved_by_user_id=u.id,
                    decision="approve" if strat != "Hash" else "override",
                    masking_strategy=strat,
                )
            )
        if fk:
            # FK: Orders.CustomerId -> Customers.Id
            fk_row = DiscoveredForeignKey(
                column_id=cols[("Orders", "CustomerId")].id,
                fk_name="FK_Orders_CustomerId",
                referenced_schema="dbo",
                referenced_table="Customers",
                referenced_column="Id",
            )
            session.add(fk_row)
        session.commit()
        return j.id, cols


def test_orchestrator_prepare_idempotent(monkeypatch, tmp_path):
    job_id, _ = _seed_orchestrator(monkeypatch, tmp_path, fk=False)
    with session_scope() as session:
        orch = MaskingOrchestrator(session)
        ctx1 = orch.prepare(job_id)
        ctx2 = orch.prepare(job_id)
        # Same job_id, same salt+key (idempotent).
        assert ctx1.salt == ctx2.salt
        assert ctx1.token_key == ctx2.token_key


def test_orchestrator_load_resolves_approvals(monkeypatch, tmp_path):
    job_id, _ = _seed_orchestrator(monkeypatch, tmp_path, fk=False)
    with session_scope() as session:
        MaskingOrchestrator(session).prepare(job_id)
        ctx = MaskingOrchestrator(session).load(job_id)
    assert ("Customers", "Email") in ctx._column_specs
    assert ctx._column_specs[("Customers", "Email")].strategy == "Fake Email"
    assert ("Customers", "Id") in ctx._column_specs
    assert ctx._column_specs[("Customers", "Id")].strategy == "Hash"


def test_orchestrator_propagates_pk_strategy_to_fk(monkeypatch, tmp_path):
    job_id, _ = _seed_orchestrator(monkeypatch, tmp_path, fk=True)
    with session_scope() as session:
        MaskingOrchestrator(session).prepare(job_id)
        ctx = MaskingOrchestrator(session).load(job_id)
    # Orders.CustomerId is an FK to Customers.Id; it should pick up Hash.
    assert ("Orders", "CustomerId") in ctx._column_specs
    assert ctx._column_specs[("Orders", "CustomerId")].strategy == "Hash"


def test_orchestrator_mask_returns_unchanged_for_unapproved_column(monkeypatch, tmp_path):
    job_id, _ = _seed_orchestrator(monkeypatch, tmp_path, fk=False)
    with session_scope() as session:
        MaskingOrchestrator(session).prepare(job_id)
        ctx = MaskingOrchestrator(session).load(job_id)
        out = MaskingOrchestrator(session).mask(ctx, "Customers", "NotApprovedColumn", "value")
    assert out == "value"


def test_orchestrator_mask_emits_token_vault_row(monkeypatch, tmp_path):
    job_id, _ = _seed_orchestrator(monkeypatch, tmp_path, fk=False)
    with session_scope() as session:
        MaskingOrchestrator(session).prepare(job_id)
        # The seed approved Customers.Email as Fake Email; flip it to Tokenization.
        from app.models.entities import ClassificationResult, Approval

        cls = (
            session.query(ClassificationResult)
            .filter_by(
                job_id=job_id, schema_name="dbo", table_name="Customers", column_name="Email"
            )
            .one()
        )
        cls.suggested_strategy = "Tokenization"
        apv = session.query(Approval).filter_by(classification_result_id=cls.id).one()
        apv.decision = "override"
        apv.masking_strategy = "Tokenization"
        session.commit()
        ctx = MaskingOrchestrator(session).load(job_id)
        tokenized = MaskingOrchestrator(session).mask(ctx, "Customers", "Email", "ada@example.com")
        assert isinstance(tokenized, str) and len(tokenized) == 64
        from app.models.entities import TokenVault

        row = (
            session.query(TokenVault)
            .filter_by(
                job_id=job_id,
                table_name="Customers",
                column_name="Email",
                tokenized_value=tokenized,
            )
            .one()
        )
        assert row.original_value == "ada@example.com"


def test_orchestrator_wipe_blocks_future_load(monkeypatch, tmp_path):
    job_id, _ = _seed_orchestrator(monkeypatch, tmp_path, fk=False)
    with session_scope() as session:
        MaskingOrchestrator(session).prepare(job_id)
    with session_scope() as session:
        MaskingOrchestrator(session).wipe(job_id)
    with session_scope() as session:
        with pytest.raises(MaskingOrchestratorError, match="wiped"):
            MaskingOrchestrator(session).load(job_id)


def test_orchestrator_pk_to_fk_deterministic_mapping(monkeypatch, tmp_path):
    """If the PK and FK both use Hash, the same CustomerId value
    masked under the PK strategy must equal the same value masked
    under the FK strategy (referential integrity).
    """
    job_id, _ = _seed_orchestrator(monkeypatch, tmp_path, fk=True)
    with session_scope() as session:
        MaskingOrchestrator(session).prepare(job_id)
        ctx = MaskingOrchestrator(session).load(job_id)
        orch = MaskingOrchestrator(session)
        pk_out = orch.mask(ctx, "Customers", "Id", 42)
        fk_out = orch.mask(ctx, "Orders", "CustomerId", 42)
    assert pk_out == fk_out
