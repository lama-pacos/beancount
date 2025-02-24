import Decimal from 'decimal.js';

export interface Meta {
    filename: string;
    lineno: number;
    original?: string;
}

export interface Amount {
    number: Decimal;
    currency: string;
}

export interface Position {
    units: Amount;
    cost?: Cost;
    price?: Amount;
}

export interface Cost {
    number: Decimal;
    currency: string;
    date?: Date;
    label?: string;
}

export interface Posting {
    account: string;
    position: Position;
    meta?: Meta;
}

export interface Transaction {
    type: 'transaction';
    date: Date;
    meta: Meta;
    flag: string;
    payee?: string;
    narration: string;
    tags: Set<string>;
    links: Set<string>;
    postings: Posting[];
}

export interface Account {
    type: 'account';
    date: Date;
    meta: Meta;
    name: string;
    currencies?: string[];
}
