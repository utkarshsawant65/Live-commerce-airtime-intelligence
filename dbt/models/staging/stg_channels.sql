{#
    Staging: channels. Reads from the governed SEED (dim_channel.csv), not raw —
    the seed is the curated reference copy. This keeps channel
    classification (broadcast/online/phone) under version control rather than at the
    mercy of whatever the generator emitted.
#}

select
    channel_id,
    trim(channel_name) as channel_name,
    lower(trim(channel_type)) as channel_type
from {{ ref('seed_channel') }}
