-- Ejecutar en Supabase: SQL Editor > New query > Run

-- Tabla de la API Mascotas
create table if not exists mascotas (
    id bigint generated always as identity primary key,
    nombre text not null,
    especie text not null check (especie in ('perro', 'gato', 'otro')),
    raza text,
    edad_meses int not null check (edad_meses >= 0),
    descripcion text,
    foto_url text,
    estado text not null default 'disponible'
        check (estado in ('disponible', 'en_proceso', 'adoptada')),
    created_at timestamptz not null default now()
);

-- Tabla de la API Adopciones
-- No lleva foreign key a mascotas a propósito: cada servicio es dueño de sus datos
-- y Adopciones consulta la existencia de la mascota por HTTP.
create table if not exists solicitudes (
    id bigint generated always as identity primary key,
    mascota_id bigint not null,
    nombre_solicitante text not null,
    correo text not null,
    telefono text,
    motivo text,
    estado text not null default 'pendiente'
        check (estado in ('pendiente', 'aprobada', 'rechazada')),
    created_at timestamptz not null default now(),
    fecha_decision timestamptz
);

-- Regla de negocio: una mascota no puede tener dos adopciones aprobadas.
-- Aunque dos aprobaciones lleguen al mismo tiempo, la base rechaza la segunda.
create unique index if not exists uq_una_aprobada_por_mascota
    on solicitudes (mascota_id)
    where estado = 'aprobada';

create index if not exists idx_solicitudes_mascota on solicitudes (mascota_id);

-- Datos de prueba
insert into mascotas (nombre, especie, raza, edad_meses, descripcion) values
    ('Canela', 'perro', 'Criollo', 18, 'Juguetona, convive bien con niños.'),
    ('Mishi', 'gato', 'Angora mestizo', 8, 'Tranquila, ya está esterilizada.'),
    ('Rocco', 'perro', 'Pitbull mestizo', 36, 'Necesita patio o paseos largos.');
