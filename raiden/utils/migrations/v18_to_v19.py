import json

from web3 import Web3

from raiden.exceptions import InvalidDBData
from raiden.storage.sqlite import EventRecord, SQLiteStorage, StateChangeRecord
from raiden.utils.typing import Any, BlockNumber, Dict

SOURCE_VERSION = 18
TARGET_VERSION = 19


class BlockHashCache(object):
    """A small cache for blocknumber to blockhashes to optimize this migration a bit

    This cache lives only during the v18->v19 migration where numerous RPC calls are
    expected to be made, many of them probably querying the same block_number -> block_hash.
    To save RPC calls and make it a bit faster we keep this short-lived cache for the duration
    of the migration
    """
    def __init__(self, web3: Web3):
        self.web3 = web3
        self.mapping = {}

    def get(self, block_number: BlockNumber) -> str:
        """Given a block number returns the hex representation of the blockhash"""
        if block_number in self.mapping:
            return self.mapping[block_number]

        block_hash = self.web3.eth.getBlock(block_number)['hash']
        block_hash = block_hash.hex()
        self.mapping[block_number] = block_hash
        return block_hash


def _add_blockhash_to_state_changes(storage: SQLiteStorage, cache: BlockHashCache) -> None:
    """Adds blockhash to ContractReceiveXXX and ActionInitChain state changes"""
    state_changes = storage.get_all_state_changes()
    updated_state_changes = []
    for state_change in state_changes:
        data = json.loads(state_change.data)
        affected_state_change = (
            'raiden.transfer.state_change.ContractReceive' in data['_type'] or
            'raiden.transfer.state_change.ActionInitChain' in data['_type']
        )
        if not affected_state_change:
            continue

        assert 'block_hash' not in data, 'v18 state changes cant contain blockhash'
        block_number = int(data['block_number'])
        data['block_hash'] = cache.get(block_number)

        updated_state_changes.append(StateChangeRecord(
            state_change_identifier=state_change.state_change_identifier,
            data=json.dumps(data),
        ))

    storage.update_state_changes(updated_state_changes)


def _add_blockhash_to_events(storage: SQLiteStorage, cache: BlockHashCache) -> None:
    """Adds blockhash to all ContractSendXXX events"""
    events = storage.get_all_event_records()
    updated_events = []
    for event in events:
        data = json.loads(event.data)
        if 'raiden.transfer.events.ContractSend' not in data['_type']:
            continue

        assert 'triggered_by_block_hash' not in data, 'v18 events cant contain blockhash'
        # Get the state_change that triggered the event and if it has
        # a block number get its hash. If not fall back to latest.
        matched_state_changes = storage.get_statechanges_by_identifier(
            from_identifier=event.state_change_identifier,
            to_identifier=event.state_change_identifier,
        )
        result_length = len(matched_state_changes)
        msg = 'multiple state changes should not exist for the same identifier'
        assert result_length == 1, msg

        statechange_data = json.loads(matched_state_changes[0])
        if 'block_hash' in statechange_data:
            block_hash = statechange_data['block_hash']
        elif 'block_number' in statechange_data:
            block_number = int(statechange_data['block_number'])
            block_hash = cache.get(block_number)
        data['triggered_by_block_hash'] = block_hash

        updated_events.append(EventRecord(
            event_identifier=event.event_identifier,
            state_change_identifier=event.state_change_identifier,
            data=json.dumps(data),
        ))

    storage.update_events(updated_events)


def _transform_snapshot(
        raw_snapshot: Dict[Any, Any],
        storage: SQLiteStorage,
        cache: BlockHashCache,
) -> str:
    """Upgrades a single snapshot by adding the blockhash to it and to any pending transactions"""
    snapshot = json.loads(raw_snapshot)
    block_number = int(snapshot['block_number'])
    snapshot['block_hash'] = cache.get(block_number)

    all_events = storage.get_all_event_records()
    pending_transactions = snapshot['pending_transactions']
    new_pending_transactions = []
    for transaction_data in pending_transactions:
        found_blockhash = None
        if 'raiden.transfer.events.ContractSend' not in transaction_data['_type']:
            new_pending_transactions.append(transaction_data)
            continue

        # For each pending transaction find the corresponding DB event record.
        # Unfortunately can't do a DB query since the pending transaction only has
        # raw data and no event identifier so the only thing I can think of is to
        # iterate all the known events.
        for event in all_events:
            event_record_data = json.loads(event.data)
            block_hash = event_record_data.pop('triggered_by_block_hash')
            if event_record_data == transaction_data:
                # found the event record in the DB. The snapshot transformation comes after
                # the events table upgrade so the event should already contain the blockhash
                found_blockhash = block_hash
                break

        if not found_blockhash:
            raise InvalidDBData(
                'Error during v18 -> v19 upgrade. Could not find a database event '
                'table entry for a pending transaction.',
            )

        transaction_data['triggered_by_block_hash'] = block_hash
        new_pending_transactions.append(transaction_data)

    snapshot['pending_transactions'] = new_pending_transactions
    return json.dumps(snapshot)


def _transform_snapshots_for_blockhash(storage: SQLiteStorage, cache: BlockHashCache) -> None:
    """Upgrades the snapshots by adding the blockhash to it and to any pending transactions"""
    for snapshot in storage.get_snapshots():
        new_snapshot = _transform_snapshot(
            raw_snapshot=snapshot.data,
            storage=storage,
            cache=cache,
        )
        storage.update_snapshot(snapshot.identifier, new_snapshot)


def upgrade_v18_to_v19(
        storage: SQLiteStorage,
        old_version: int,
        current_version: int,
        web3: Web3,
) -> int:
    if old_version == SOURCE_VERSION:
        cache = BlockHashCache(web3)
        _add_blockhash_to_state_changes(storage, cache)
        _add_blockhash_to_events(storage, cache)
        # The snapshot transformation should come last
        _transform_snapshots_for_blockhash(storage, cache)

    return TARGET_VERSION
