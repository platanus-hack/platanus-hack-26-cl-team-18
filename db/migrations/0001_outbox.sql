-- Transactional outbox.
-- PostgresUnitOfWork writes integration events here in the same transaction
-- as the aggregate state; a separate dispatcher publishes them.

CREATE TABLE outbox_event (
    event_id uuid NOT NULL,
    event_type text NOT NULL,
    schema_version integer NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    organization_id uuid,
    aggregate_type text NOT NULL,
    aggregate_id uuid NOT NULL,
    aggregate_version integer NOT NULL,
    payload jsonb NOT NULL,
    headers jsonb DEFAULT '{}'::jsonb NOT NULL,
    correlation_id uuid,
    causation_id uuid,
    request_id uuid,
    idempotency_key text,
    source text,
    actor_type text,
    actor_id text,
    status text DEFAULT 'pending'::text NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 25 NOT NULL,
    locked_until timestamp with time zone,
    locked_by text,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_outbox_aggregate_type_nonblank CHECK ((btrim(aggregate_type) <> ''::text)),
    CONSTRAINT chk_outbox_event_type_nonblank CHECK ((btrim(event_type) <> ''::text)),
    CONSTRAINT chk_outbox_headers_object CHECK ((jsonb_typeof(headers) = 'object'::text)),
    CONSTRAINT chk_outbox_lock_consistency CHECK ((((locked_until IS NULL) AND (locked_by IS NULL)) OR ((locked_until IS NOT NULL) AND (locked_by IS NOT NULL)))),
    CONSTRAINT chk_outbox_payload_object CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT outbox_event_aggregate_version_check CHECK ((aggregate_version >= 1)),
    CONSTRAINT outbox_event_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT outbox_event_max_attempts_check CHECK ((max_attempts >= 1)),
    CONSTRAINT outbox_event_schema_version_check CHECK ((schema_version >= 1)),
    CONSTRAINT outbox_event_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'publishing'::text, 'published'::text, 'failed'::text, 'dead'::text])))
);

ALTER TABLE ONLY outbox_event
    ADD CONSTRAINT outbox_event_pkey PRIMARY KEY (event_id);


CREATE INDEX ix_outbox_aggregate_order ON outbox_event USING btree (aggregate_id, aggregate_version);
CREATE INDEX ix_outbox_correlation ON outbox_event USING btree (correlation_id);
CREATE INDEX ix_outbox_due ON outbox_event USING btree (status, available_at) WHERE (status = ANY (ARRAY['pending'::text, 'failed'::text]));
CREATE INDEX ix_outbox_locked_until ON outbox_event USING btree (locked_until) WHERE (locked_until IS NOT NULL);
CREATE INDEX ix_outbox_organization_time ON outbox_event USING btree (organization_id, occurred_at DESC);
