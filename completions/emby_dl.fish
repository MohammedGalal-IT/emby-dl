# fish completion for emby_dl and emby-dl

function __fish_emby_dl_needs_command
    set -l cmd (commandline -opc)
    if [ (count $cmd) -eq 1 ]
        return 0
    end
    return 1
end

function __fish_emby_dl_using_subcommand
    set -l cmd (commandline -opc)
    set -e cmd[1]
    for arg in $cmd
        if not string match -q -- "-*" $arg
            if test "$arg" = "$argv[1]"
                return 0
            end
        end
    end
    return 1
end

for c in emby_dl emby-dl
    complete -c $c -f

    # Global options
    complete -c $c -n "__fish_emby_dl_needs_command" -l config -s c -r -d "Path to config file"
    complete -c $c -n "__fish_emby_dl_needs_command" -l downloads-dir -r -a "(__fish_complete_directories)" -d "Override downloads directory"
    complete -c $c -n "__fish_emby_dl_needs_command" -l json -d "Machine-readable output"
    complete -c $c -n "__fish_emby_dl_needs_command" -l version -s V -d "Show version"
    complete -c $c -n "__fish_emby_dl_needs_command" -l help -s h -d "Show help"

    # Commands
    complete -c $c -n "__fish_emby_dl_needs_command" -a download -d "Download episodes"
    complete -c $c -n "__fish_emby_dl_needs_command" -a queue -d "Manage and run multi-series queues"
    complete -c $c -n "__fish_emby_dl_needs_command" -a plan -d "Resolve selection and show plan"
    complete -c $c -n "__fish_emby_dl_needs_command" -a status -d "Show live download progress"
    complete -c $c -n "__fish_emby_dl_needs_command" -a verify -d "Verify recorded files against server"
    complete -c $c -n "__fish_emby_dl_needs_command" -a watch -d "Poll for new episodes"
    complete -c $c -n "__fish_emby_dl_needs_command" -a dataset -d "Dataset maintenance"
    complete -c $c -n "__fish_emby_dl_needs_command" -a search -d "Search for series"
    complete -c $c -n "__fish_emby_dl_needs_command" -a list -d "List seasons and episode counts"
    complete -c $c -n "__fish_emby_dl_needs_command" -a config -d "Show configuration"
    complete -c $c -n "__fish_emby_dl_needs_command" -a auth -d "Check server and token health"
    complete -c $c -n "__fish_emby_dl_needs_command" -a login -d "Authenticate and store token"
    complete -c $c -n "__fish_emby_dl_needs_command" -a doctor -d "Diagnose health"
    complete -c $c -n "__fish_emby_dl_needs_command" -a init -d "Initialize configuration and downloads folder"

    # Options for download
    complete -c $c -n "__fish_emby_dl_using_subcommand download" -l series-id -r -d "Target series ID"
    complete -c $c -n "__fish_emby_dl_using_subcommand download" -l source -r -a "1 2 3 max" -d "Media source"
    complete -c $c -n "__fish_emby_dl_using_subcommand download" -l include-specials -d "Include season 0 with all"
    complete -c $c -n "__fish_emby_dl_using_subcommand download" -l progress -r -a "auto plain json none"
    complete -c $c -n "__fish_emby_dl_using_subcommand download" -l notify -d "Desktop notification"
    complete -c $c -n "__fish_emby_dl_using_subcommand download" -l webhook -r -d "Webhook URL"
    complete -c $c -n "__fish_emby_dl_using_subcommand download" -l json

    # Options for queue
    complete -c $c -n "__fish_emby_dl_using_subcommand queue" -a "add list run clear remove plan" -d "Queue action"
    complete -c $c -n "__fish_emby_dl_using_subcommand queue" -l file -s f -r -d "Queue file path"
    complete -c $c -n "__fish_emby_dl_using_subcommand queue" -l series-id -r -d "Target series ID"
    complete -c $c -n "__fish_emby_dl_using_subcommand queue" -l source -r -a "1 2 3 max" -d "Media source"
    complete -c $c -n "__fish_emby_dl_using_subcommand queue" -l include-specials -d "Include season 0 with all"
    complete -c $c -n "__fish_emby_dl_using_subcommand queue" -l progress -r -a "auto plain json none"
    complete -c $c -n "__fish_emby_dl_using_subcommand queue" -l notify -d "Desktop notification"
    complete -c $c -n "__fish_emby_dl_using_subcommand queue" -l webhook -r -d "Webhook URL"
    complete -c $c -n "__fish_emby_dl_using_subcommand queue" -l json

    # Options for dataset
    complete -c $c -n "__fish_emby_dl_using_subcommand dataset" -l stats -d "Statistics"
    complete -c $c -n "__fish_emby_dl_using_subcommand dataset" -l rebuild -d "Rebuild from disk"
    complete -c $c -n "__fish_emby_dl_using_subcommand dataset" -l prune -d "Remove missing entries"
    complete -c $c -n "__fish_emby_dl_using_subcommand dataset" -l check -d "Check inconsistencies"
    complete -c $c -n "__fish_emby_dl_using_subcommand dataset" -l sync -d "Sync per-series datasets"
    complete -c $c -n "__fish_emby_dl_using_subcommand dataset" -l dedupe -d "Deduplicate records"
    complete -c $c -n "__fish_emby_dl_using_subcommand dataset" -l json

    # Options for auth
    complete -c $c -n "__fish_emby_dl_using_subcommand auth" -l reset-caps -d "Clear capabilities cache"
    complete -c $c -n "__fish_emby_dl_using_subcommand auth" -l json

    # Options for doctor
    complete -c $c -n "__fish_emby_dl_using_subcommand doctor" -l offline -d "Skip server checks"
    complete -c $c -n "__fish_emby_dl_using_subcommand doctor" -l json

    # Options for init
    complete -c $c -n "__fish_emby_dl_using_subcommand init" -l server-url -r -d "Emby server URL"
    complete -c $c -n "__fish_emby_dl_using_subcommand init" -l username -r -d "Username"
    complete -c $c -n "__fish_emby_dl_using_subcommand init" -l password -r -d "Password"
    complete -c $c -n "__fish_emby_dl_using_subcommand init" -l json
end
