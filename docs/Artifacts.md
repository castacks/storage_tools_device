# Storage Tools Device Artifacts

The Storage Tools Device creates additional files to speed up processing.  These will always in the form of `{original_file_name}.md5` and `{original_file_name}.metadata`. These files will only be created in the `watch` directories as defined by the config yaml file.  These files can be safely removed. They will be regenerated when the system scans again.
