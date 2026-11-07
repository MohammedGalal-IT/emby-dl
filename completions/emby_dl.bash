# bash completion for emby_dl / emby-dl
_emby_dl_completions() {
    local cur prev words cword
    _init_completion || return

    local commands="download plan status verify watch dataset search list config auth login doctor init"
    local global_opts="--config -c --downloads-dir --json --version -h --help"

    local cmd=""
    local i
    for ((i=1; i < cword; i++)); do
        local word="${words[i]}"
        if [[ "$word" != -* ]]; then
            case " $commands " in
                *" $word "*)
                    cmd="$word"
                    break
                    ;;
            esac
        fi
    done

    if [[ -z "$cmd" ]]; then
        if [[ "$cur" == -* ]]; then
            COMPREPLY=($(compgen -W "$global_opts" -- "$cur"))
        else
            COMPREPLY=($(compgen -W "$commands" -- "$cur"))
        fi
        return 0
    fi

    case "$prev" in
        --config|-c|--downloads-dir)
            _filedir
            return 0
            ;;
        --progress)
            COMPREPLY=($(compgen -W "auto plain json none" -- "$cur"))
            return 0
            ;;
        --source)
            COMPREPLY=($(compgen -W "1 2 3 max" -- "$cur"))
            return 0
            ;;
    esac

    case "$cmd" in
        download)
            COMPREPLY=($(compgen -W "--series-id --source --include-specials --progress --notify --webhook --json" -- "$cur"))
            ;;
        plan)
            COMPREPLY=($(compgen -W "--series-id --source --include-specials --json" -- "$cur"))
            ;;
        status)
            COMPREPLY=($(compgen -W "--json" -- "$cur"))
            ;;
        verify)
            COMPREPLY=($(compgen -W "--series-id --fix --progress --json" -- "$cur"))
            ;;
        watch)
            COMPREPLY=($(compgen -W "--series-id --source --include-specials --interval --once --notify --webhook --progress --json" -- "$cur"))
            ;;
        dataset)
            COMPREPLY=($(compgen -W "--stats --rebuild --prune --check --sync --dedupe --json" -- "$cur"))
            ;;
        search)
            COMPREPLY=($(compgen -W "--limit --json" -- "$cur"))
            ;;
        list)
            COMPREPLY=($(compgen -W "--series-id --json" -- "$cur"))
            ;;
        config)
            COMPREPLY=($(compgen -W "--json" -- "$cur"))
            ;;
        auth)
            COMPREPLY=($(compgen -W "--reset-caps --json" -- "$cur"))
            ;;
        login)
            COMPREPLY=($(compgen -W "--username --password --json" -- "$cur"))
            ;;
        doctor)
            COMPREPLY=($(compgen -W "--offline --json" -- "$cur"))
            ;;
        init)
            COMPREPLY=($(compgen -W "--server-url --username --password --json" -- "$cur"))
            ;;
    esac
}

complete -F _emby_dl_completions emby_dl
complete -F _emby_dl_completions emby-dl
