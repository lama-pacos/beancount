import { Parser } from './index';

describe('Parser', () => {
    it('should parse a simple transaction', () => {
        const input = `2024-02-24 * "Grocery Store" "Buy some food"`;
        const parser = new Parser(input);
        const entries = parser.parse();

        expect(entries).toHaveLength(1);
        const transaction = entries[0];
        expect(transaction).toMatchObject({
            type: 'transaction',
            date: new Date(2024, 1, 24),
            flag: '*',
            payee: 'Grocery Store',
            narration: 'Buy some food'
        });
    });

    it('should parse an account opening', () => {
        const input = `2024-02-24 open Assets:Checking USD,EUR`;
        const parser = new Parser(input);
        const entries = parser.parse();

        expect(entries).toHaveLength(1);
        const account = entries[0];
        expect(account).toMatchObject({
            type: 'account',
            date: new Date(2024, 1, 24),
            name: 'Assets:Checking',
            currencies: ['USD', 'EUR']
        });
    });

    it('should skip comments and empty lines', () => {
        const input = `
            ; This is a comment
            2024-02-24 open Assets:Checking USD

            ; Another comment
            2024-02-24 * "Grocery Store" "Buy some food"
        `;
        const parser = new Parser(input);
        const entries = parser.parse();

        expect(entries).toHaveLength(2);
    });
});
