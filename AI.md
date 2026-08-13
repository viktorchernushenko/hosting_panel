# Historical AI integration note

The MyH AI integration was removed in release 2.4.0 after production usage and dependency review showed two runtime-recommendation events and no site/log diagnostic use. Runtime selection now uses deterministic project markers, while Logs uses fixed common-error classifications. The panel has no AI routes, provider, credentials, model service or inference dependency.

Historical `ai.*` entries remain in the shared audit log to preserve operational history; no AI-specific table or schema migration exists.
