-- Foreign keys added by the deal, estimate and pilot migrations had no covering index, so a
-- delete on the parent table (or a join from the child) scanned the child. One index each.
create index if not exists deal_document_document_version_id_idx
  on public.deal_document (document_version_id);
create index if not exists deal_event_issuer_id_idx
  on public.deal_event (issuer_id);
create index if not exists deal_participant_issuer_id_idx
  on public.deal_participant (issuer_id);
create index if not exists deal_state_transition_document_version_id_idx
  on public.deal_state_transition (document_version_id);
create index if not exists deal_state_transition_evidence_id_idx
  on public.deal_state_transition (evidence_id);
create index if not exists deal_structure_link_evidence_id_idx
  on public.deal_structure_link (evidence_id);
create index if not exists deal_structure_link_successor_issuer_id_idx
  on public.deal_structure_link (successor_issuer_id);
create index if not exists deal_term_version_document_version_id_idx
  on public.deal_term_version (document_version_id);
create index if not exists deal_term_version_evidence_id_idx
  on public.deal_term_version (evidence_id);
create index if not exists deal_term_version_supersedes_id_idx
  on public.deal_term_version (supersedes_id);
create index if not exists pilot_defect_parent_id_idx
  on public.pilot_defect (parent_id);
create index if not exists public_estimate_snapshot_corrected_by_id_idx
  on public.public_estimate_snapshot (corrected_by_id);
