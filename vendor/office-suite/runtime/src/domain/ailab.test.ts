import {describe,it,expect} from 'vitest';
import ExcelJS from 'exceljs';
import {paginate} from './model.ts';
import {parseXlsx} from '../adapters/parse/xlsx.ts';
import {parseLocator} from './anchor.ts';

describe('AILab continuous coverage',()=>{
  it('does not consume an unread middle of one oversized unit',()=>{
    const original='A'.repeat(50000)+'UNIQUE_MIDDLE'+'Z'.repeat(50000);
    let cursor=0;const parts=[];
    do {const page=paginate([original],cursor,8000);parts.push(page.text);cursor=page.next;} while(cursor!==null);
    expect(parts.join('')).toBe(original);
  });
  it('reads every row and every horizontal batch of a wide sheet',async()=>{
    const workbook=new ExcelJS.Workbook();const sheet=workbook.addWorksheet('Wide');
    sheet.getCell('A1').value='first';sheet.getCell('BR301').value='tail-at-column-70-row-301';
    const doc=await parseXlsx(new Uint8Array(await workbook.xlsx.writeBuffer()),'wide.xlsx');
    let cursor=0;const parts=[];
    do {const page=doc.read(parseLocator('xlsx','Wide'),cursor);parts.push(page.text);cursor=page.next;} while(cursor!==null);
    expect(parts.join('\n')).toContain('tail-at-column-70-row-301');
    expect(parts.join('\n')).not.toContain('columns BI');
  });
});
