# Migrações de banco (Alembic)

Com o ambiente virtual ativo:

```bash
alembic upgrade head
```

Para criar nova revisão:

```bash
alembic revision -m "descrição da alteração"
```

Para aplicar rollback de uma revisão:

```bash
alembic downgrade -1
```

A URL do banco é resolvida via `DATABASE_PATH` (SQLite local) quando definida.
