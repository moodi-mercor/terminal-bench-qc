# Count ERROR lines in the service log

Read `/app/logs/service.log`. Each line begins with a level field (`INFO`,
`WARN`, or `ERROR`). Write the number of lines whose level is exactly `ERROR`
to `/app/out/error_count.txt` as a single integer followed by a newline.
