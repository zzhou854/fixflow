"""Property-query MCP contracts."""

from uuid import UUID

from app.property_operations.contracts.common import MCPContractModel, ReadRequest


class GetResidentPropertyRequest(ReadRequest):
    resident_id: UUID
    property_id: UUID


class ResidentPropertyData(MCPContractModel):
    resident_id: UUID
    property_id: UUID
    community_name: str
    building_no: str
    unit_no: str
    room_no: str
    address_text: str
