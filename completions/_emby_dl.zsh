#compdef emby_dl emby-dl

_emby_dl() {
    local -a commands
    commands=(
        'download:Download episodes'
        'queue:Manage and run multi-series download queues'
        'plan:Resolve selection and show what would download'
        'status:Show live download progress'
        'verify:Verify recorded files against the server'
        'watch:Poll for new episodes and download them'
        'dataset:Show or maintain the download dataset'
        'search:Search for series'
        'list:List seasons and episode counts'
        'config:Show configuration (secrets hidden)'
        'auth:Check server and token health'
        'login:Authenticate and store a fresh token'
        'doctor:Diagnose config, server, tools and dataset'
        'init:Initialize configuration and default downloads directory'
    )

    _arguments -C \
        '--config[Path to config file]:file:_files' \
        '-c[Path to config file]:file:_files' \
        '--downloads-dir[Override downloads directory]:dir:_files -/' \
        '--json[Machine-readable output]' \
        '--version[Show version]' \
        '1: :->command' \
        '*:: :->args'

    case $state in
        command)
            _describe -t commands 'emby_dl commands' commands
            ;;
        args)
            case $words[1] in
                download)
                    _arguments \
                        '--series-id[Target specific series ID]:id:' \
                        '--source[Media source index or max]:source:(1 2 3 max)' \
                        '--include-specials[Include season 0 / specials with all]' \
                        '--progress[Progress format]:mode:(auto plain json none)' \
                        '--notify[Desktop notification when done]' \
                        '--webhook[POST completion webhook]:url:' \
                        '--json[Machine-readable output]' \
                        '*:args:'
                    ;;
                queue)
                    _arguments \
                        '--file[Queue file path]:file:_files' \
                        '-f[Queue file path]:file:_files' \
                        '--series-id[Target specific series ID]:id:' \
                        '--source[Media source index or max]:source:(1 2 3 max)' \
                        '--include-specials[Include season 0 / specials with all]' \
                        '--progress[Progress format]:mode:(auto plain json none)' \
                        '--notify[Desktop notification when done]' \
                        '--webhook[POST completion webhook]:url:' \
                        '--json[Machine-readable output]' \
                        '1:action:(add list run clear remove plan)' \
                        '*:args:'
                    ;;
                plan)
                    _arguments \
                        '--series-id[Target specific series ID]:id:' \
                        '--source[Media source index or max]:source:(1 2 3 max)' \
                        '--include-specials[Include season 0 / specials with all]' \
                        '--json[Machine-readable output]' \
                        '*:args:'
                    ;;
                verify)
                    _arguments \
                        '--series-id[Target specific series ID]:id:' \
                        '--fix[Re-download bad or missing files]' \
                        '--progress[Progress format]:mode:(auto plain json none)' \
                        '--json[Machine-readable output]' \
                        '*:args:'
                    ;;
                watch)
                    _arguments \
                        '--series-id[Target specific series ID]:id:' \
                        '--source[Media source index or max]:source:(1 2 3 max)' \
                        '--include-specials[Include specials with all]' \
                        '--interval[Polling interval in seconds]:seconds:' \
                        '--once[Run one check then exit]' \
                        '--notify[Desktop notification]' \
                        '--webhook[POST completion webhook]:url:' \
                        '--progress[Progress format]:mode:(auto plain json none)' \
                        '--json[Machine-readable output]' \
                        '*:args:'
                    ;;
                dataset)
                    _arguments \
                        '--stats[Show dataset statistics]' \
                        '--rebuild[Rebuild datasets from files on disk]' \
                        '--prune[Remove records whose files are missing]' \
                        '--check[Report inconsistencies]' \
                        '--sync[Regenerate per-series datasets from central]' \
                        '--dedupe[Remove duplicate entries]' \
                        '--json[Machine-readable output]' \
                        '*:args:'
                    ;;
                auth)
                    _arguments \
                        '--reset-caps[Clear cached server capabilities]' \
                        '--json[Machine-readable output]'
                    ;;
                doctor)
                    _arguments \
                        '--offline[Skip live server checks]' \
                        '--json[Machine-readable output]'
                    ;;
                search)
                    _arguments \
                        '--limit[Max search results]:count:' \
                        '--json[Machine-readable output]' \
                        '*:args:'
                    ;;
                list)
                    _arguments \
                        '--series-id[Target specific series ID]:id:' \
                        '--json[Machine-readable output]' \
                        '*:args:'
                    ;;
                login)
                    _arguments \
                        '--username[Username]:name:' \
                        '--password[Password]:pwd:' \
                        '--json[Machine-readable output]'
                    ;;
                init)
                    _arguments \
                        '--server-url[Emby server URL]:url:' \
                        '--username[Username]:name:' \
                        '--password[Password]:pwd:' \
                        '--json[Machine-readable output]'
                    ;;
            esac
            ;;
    esac
}

_emby_dl "$@"
