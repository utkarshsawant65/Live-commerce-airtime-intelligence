{# Channels from the seed_channel seed, which is the maintained reference list (not RAW). #}

select
    channel_id,
    trim(channel_name) as channel_name,
    lower(trim(channel_type)) as channel_type
from {{ ref('seed_channel') }}
