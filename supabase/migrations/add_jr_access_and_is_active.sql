-- Add jr_access and is_active columns to resupd_allowed_users
ALTER TABLE resupd_allowed_users
  ADD COLUMN IF NOT EXISTS jr_access text NOT NULL DEFAULT 'subcon',
  ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;

-- Add component columns to recruiter_signatures for editable sig fields
ALTER TABLE recruiter_signatures
  ADD COLUMN IF NOT EXISTS sig_name      text,
  ADD COLUMN IF NOT EXISTS sig_job_title text,
  ADD COLUMN IF NOT EXISTS sig_phone     text;
