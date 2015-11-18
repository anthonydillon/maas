# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Model for a block devices partition table."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = [
    'PartitionTable',
    ]

from django.core.exceptions import ValidationError
from django.db.models import (
    CharField,
    ForeignKey,
    Sum,
)
from maasserver import DefaultMeta
from maasserver.enum import (
    PARTITION_TABLE_TYPE,
    PARTITION_TABLE_TYPE_CHOICES,
)
from maasserver.models.blockdevice import BlockDevice
from maasserver.models.cleansave import CleanSave
from maasserver.models.partition import (
    Partition,
    PARTITION_ALIGNMENT_SIZE,
)
from maasserver.models.timestampedmodel import TimestampedModel
from maasserver.utils.converters import round_size_to_nearest_block

# The first 2MiB of the device are used by the partition table and grub. We'll
# reserve the first 4MiB to make sure all partitions stay aligned to 4MB across
# the device.
INITIAL_PARTITION_OFFSET = 4 * 1024 * 1024

# An additional 1MiB of space is left open at the end of the disk to allow for
# the extra MBR table.
END_OF_PARTITION_TABLE_SPACE = 1 * 1024 * 1024

# The amount of extra space a partition table will use.
PARTITION_TABLE_EXTRA_SPACE = (
    INITIAL_PARTITION_OFFSET + END_OF_PARTITION_TABLE_SPACE)


class PartitionTable(CleanSave, TimestampedModel):
    """A partition table on a block device.

    :ivar table_type: Type of partition table.
    :ivar block_device: `BlockDevice` this partition table belongs to.
    """

    class Meta(DefaultMeta):
        """Needed for South to recognize this model."""

    table_type = CharField(
        max_length=20, choices=PARTITION_TABLE_TYPE_CHOICES, default=None)

    block_device = ForeignKey(
        BlockDevice, null=False, blank=False)

    def get_node(self):
        """`Node` this partition belongs to."""
        return self.block_device.node

    def get_size(self):
        """Total usable size of partition table."""
        return round_size_to_nearest_block(
            self.block_device.size - PARTITION_TABLE_EXTRA_SPACE,
            PARTITION_ALIGNMENT_SIZE,
            False)

    def get_block_size(self):
        """Block size of partition table."""
        return self.block_device.block_size

    def get_used_size(self, ignore_partitions=[]):
        """Return the used size of partitions on the table."""
        ignore_ids = [
            partition.id
            for partition in ignore_partitions
            if partition.id is not None
        ]
        used_size = self.partitions.exclude(
            id__in=ignore_ids).aggregate(Sum('size'))['size__sum']
        if used_size is None:
            used_size = 0
        # The extra space taken by the partition table header is used space.
        return used_size + PARTITION_TABLE_EXTRA_SPACE

    def get_available_size(self, ignore_partitions=[]):
        """Return the remaining size available for partitions."""
        used_size = self.get_used_size(ignore_partitions=ignore_partitions)
        # Only report 'alignable' space as available for new partitions
        return round_size_to_nearest_block(
            self.block_device.size - used_size,
            PARTITION_ALIGNMENT_SIZE, False)

    def add_partition(self, size=None, bootable=False, uuid=None):
        """Adds a partition to this partition table, returns the added
        partition.

        If size is omitted, the partition will extend to the end of the device.

        All partition sizes will be aligned down to PARTITION_ALIGNMENT_SIZE.
        """
        if size is None:
            size = self.get_available_size()
            if self.table_type == PARTITION_TABLE_TYPE.MBR:
                size = min(size, Partition._get_mbr_max_for_block_device(
                    self.block_device))
        size = round_size_to_nearest_block(
            size, PARTITION_ALIGNMENT_SIZE, False)
        return Partition.objects.create(
            partition_table=self, size=size, uuid=uuid, bootable=bootable)

    def __unicode__(self):
        return "Partition table for {bd}".format(
            bd=self.block_device.__unicode__())

    def save(self, *args, **kwargs):
        self._set_and_validate_table_type_for_boot_disk()
        return super(PartitionTable, self).save(*args, **kwargs)

    def _set_and_validate_table_type_for_boot_disk(self):
        """Validates or set the table type if this partition table is on the
        boot disk for a node."""
        if (self.block_device is not None and
                self.block_device.node is not None):
            node = self.block_device.node
            boot_disk = node.get_boot_disk()
            # Compare the block_device.id and boot_disk.id because it is
            # possible they are not the same type. One being an instance
            # of PhysicalBlockDevice and the other being just a BlockDevice.
            # Without this comparison the wrong partition table type will be
            # placed on the boot disk.
            if boot_disk is not None and self.block_device.id == boot_disk.id:
                bios_boot_method = node.get_bios_boot_method()
                if bios_boot_method == "uefi":
                    # UEFI must always use a GPT table.
                    if not self.table_type:
                        self.table_type = PARTITION_TABLE_TYPE.GPT
                    elif self.table_type != PARTITION_TABLE_TYPE.GPT:
                        raise ValidationError({
                            "table_type": [
                                "Partition table on this node's boot disk "
                                "must be using '%s'." % (
                                    PARTITION_TABLE_TYPE.GPT)]
                            })
                else:
                    # Don't even check if its 'pxe', because we always fallback
                    # to MBR.
                    if not self.table_type:
                        self.table_type = PARTITION_TABLE_TYPE.MBR
                    elif self.table_type != PARTITION_TABLE_TYPE.MBR:
                        raise ValidationError({
                            "table_type": [
                                "Partition table on this node's boot disk "
                                "must be using '%s'." % (
                                    PARTITION_TABLE_TYPE.MBR)]
                            })

        # Force GPT for everything else.
        if not self.table_type:
            self.table_type = PARTITION_TABLE_TYPE.GPT

    def clean(self, *args, **kwargs):
        super(PartitionTable, self).clean(*args, **kwargs)
        # Circular imports.
        from maasserver.models.virtualblockdevice import VirtualBlockDevice
        # Cannot place a partition table on a logical volume.
        if self.block_device is not None:
            block_device = self.block_device.actual_instance
            if isinstance(block_device, VirtualBlockDevice):
                if block_device.filesystem_group.is_lvm():
                    raise ValidationError({
                        "block_device": [
                            "Cannot create a partition table on a "
                            "logical volume."]
                        })
                elif block_device.filesystem_group.is_bcache():
                    raise ValidationError({
                        "block_device": [
                            "Cannot create a partition table on a "
                            "Bcache volume."]
                        })
