import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'
import { api } from '../api/client'
import { ReconciliationCases } from './ReconciliationCases'

vi.mock('../api/client',async(load)=>{const actual=await load<typeof import('../api/client')>();return{...actual,api:{...actual.api,reconciliationCases:vi.fn(),recheckReconciliation:vi.fn()}}})

test('renders safe reconciliation status without dangerous resolution controls',async()=>{vi.mocked(api.reconciliationCases).mockResolvedValue({items:[{case_id:'c',operation_type:'CREATE_TICKET',status:'MANUAL_REVIEW',target_entity_id:null,attempt_count:2,evidence_status:'INCONSISTENT',resolution_code:'EVIDENCE_CONFLICT',retry_allowed:false,created_at:'2026-07-22T00:00:00Z',updated_at:'2026-07-22T00:00:00Z'}]});render(<ReconciliationCases token="token"/>);expect(await screen.findByText('需要人工审查')).toBeInTheDocument();expect(screen.queryByText('标记已提交')).not.toBeInTheDocument();expect(screen.queryByText('重发操作')).not.toBeInTheDocument()})

test('allows only pending case recheck',async()=>{vi.mocked(api.reconciliationCases).mockResolvedValue({items:[{case_id:'c',operation_type:'BOOK_APPOINTMENT',status:'PENDING',target_entity_id:null,attempt_count:1,evidence_status:null,resolution_code:null,retry_allowed:true,created_at:'2026-07-22T00:00:00Z',updated_at:'2026-07-22T00:00:00Z'}]});vi.mocked(api.recheckReconciliation).mockResolvedValue({case_id:'c',operation_type:'BOOK_APPOINTMENT',status:'PENDING',target_entity_id:null,attempt_count:1,evidence_status:null,resolution_code:null,retry_allowed:true,created_at:'2026-07-22T00:00:00Z',updated_at:'2026-07-22T00:00:00Z'});render(<ReconciliationCases token="token"/>);await userEvent.click(await screen.findByRole('button',{name:'重新检查'}));expect(api.recheckReconciliation).toHaveBeenCalledWith('token','c')})

test('opens a sanitized detail drawer from the table row',async()=>{vi.mocked(api.reconciliationCases).mockResolvedValue({items:[{case_id:'case-safe',operation_id_short:'op-safe',thread_id_short:'th-safe',operation_type:'CREATE_TICKET',status:'RESOLVED_COMMITTED',target_entity_id:null,attempt_count:1,evidence_status:'COMMITTED',resolution_code:'DURABLE_EVIDENCE_MATCHED',retry_allowed:false,created_at:'2026-07-22T00:00:00Z',updated_at:'2026-07-22T00:00:00Z'}]});render(<ReconciliationCases token="token"/>);await userEvent.click(await screen.findByText('CREATE_TICKET'));expect(await screen.findByText('对账详情')).toBeInTheDocument();expect(screen.getByText('op-safe')).toBeInTheDocument();expect(screen.queryByText('pending_operation')).not.toBeInTheDocument()})

test('renders the empty state without exposing mutation controls',async()=>{vi.mocked(api.reconciliationCases).mockResolvedValue({items:[]});render(<ReconciliationCases token="token"/>);expect(await screen.findByText('暂无对账记录')).toBeInTheDocument();expect(screen.queryByText('重发业务操作')).not.toBeInTheDocument()})
