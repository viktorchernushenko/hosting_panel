# MyH 2.9.1 release notes

MyH 2.9.1 fixes Database Studio table browsing against MySQL servers that return uppercase `INFORMATION_SCHEMA` dictionary keys. Metadata is normalized once into the existing lowercase snake_case schema used by structure, data, index, row-editor and frontend consumers.

Empty tables now render an explicit empty state. API failures remain JSON and the frontend offers retry plus a safe request ID instead of displaying Flask HTML or traceback details.

Production read-only validation covered `wp_commentmeta`, `wp_comments`, `wp_links`, `wp_options`, `wp_postmeta`, `wp_posts`, `wp_users`, and `wp_usermeta`. A disposable isolated database verified `INT`, `BIGINT`, `VARCHAR`, `TEXT`, `DECIMAL`, `DATETIME`, `NULL`, `JSON`, `BLOB`, insert, update, delete, serialization, and cleanup.
