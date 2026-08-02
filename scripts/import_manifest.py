#!/usr/bin/env python3
"""Import manifests from fd-industry-data into fd-open-data-mcp.

Usage:
    uv run scripts/import_manifest.py <manifest_path>

Example:
    uv run scripts/import_manifest.py manifests/nbs-gdp.yaml
"""

import sys
import yaml
from pathlib import Path

# Add fd-open-data-mcp to path
mcp_path = Path(__file__).parent.parent.parent / "fd-open-data-mcp"
sys.path.insert(0, str(mcp_path))

from fd_open_data_mcp.db import get_database
from fd_open_data_mcp.models import Source, Function, FunctionColumn


def import_manifest(manifest_path: str) -> None:
    """Import a manifest YAML file into fd-open-data-mcp database."""
    
    with open(manifest_path, 'r') as f:
        manifest = yaml.safe_load(f)
    
    print(f"Importing manifest: {manifest['name']}")
    
    db = get_database()
    session = db.get_session()
    
    try:
        # Check if source already exists
        existing = session.query(Source).filter_by(name=manifest['name']).first()
        if existing:
            print(f"Source '{manifest['name']}' already exists (id={existing.id}), updating...")
            source = existing
        else:
            # Create source
            source = Source(
                name=manifest['name'],
                label=manifest['label'],
                url=manifest['source_url']
            )
            session.add(source)
            session.flush()
            print(f"Created source: {source.name} (id={source.id})")
        
        # Create functions
        for func_data in manifest['functions']:
            existing_func = session.query(Function).filter_by(
                source_id=source.id,
                command=func_data['command']
            ).first()
            
            if existing_func:
                print(f"  Function '{func_data['command']}' already exists, updating...")
                func = existing_func
                func.description = func_data.get('description', '')
                func.category = func_data.get('category')
                func.frequency = func_data.get('frequency')
                func.parameters = func_data.get('parameters', [])
            else:
                func = Function(
                    source_id=source.id,
                    command=func_data['command'],
                    description=func_data.get('description', ''),
                    category=func_data.get('category'),
                    frequency=func_data.get('frequency'),
                    parameters=func_data.get('parameters', []),
                    verified=True
                )
                session.add(func)
                session.flush()
                print(f"  Created function: {func.command} (id={func.id})")
            
            # Create columns
            for col_data in func_data['columns']:
                existing_col = session.query(FunctionColumn).filter_by(
                    function_id=func.id,
                    name=col_data['name']
                ).first()
                
                if existing_col:
                    existing_col.type = col_data['type']
                    existing_col.description = col_data.get('description', '')
                    existing_col.meaning = col_data.get('meaning', 'unknown')
                else:
                    col = FunctionColumn(
                        function_id=func.id,
                        name=col_data['name'],
                        type=col_data['type'],
                        description=col_data.get('description', ''),
                        meaning=col_data.get('meaning', 'unknown')
                    )
                    session.add(col)
                    print(f"    Created column: {col.name}")
        
        session.commit()
        print(f"\n✅ Successfully imported {manifest['name']}")
        
    except Exception as e:
        session.rollback()
        print(f"\n❌ Error importing manifest: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: uv run scripts/import_manifest.py <manifest_path>")
        sys.exit(1)
    
    import_manifest(sys.argv[1])
